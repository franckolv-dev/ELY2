"""Canal Telegram : parler à Ely depuis Telegram (texte, vocal, photo), sans rien installer d'autre."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time

import httpx

from ..auth import get_user
from ..config import settings
from ..db import db, now
from ..integrations import get, put, remove
from ..notify import telegram_send

log = logging.getLogger("ely.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
_username = ""
_typing: dict[int, float] = {}
RETRY_S = 5  # pause après une réponse en erreur de Telegram (évite de le marteler)


def bot_username() -> str:
    return _username


async def call(method: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=70) as c:
        r = await c.post(API.format(token=settings.telegram_bot_token, method=method), json=params)
        return r.json()


async def download(file_id: str) -> bytes:
    info = await call("getFile", file_id=file_id)
    path = info["result"]["file_path"]
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{path}")
        return r.content


def user_for_chat(chat_id: int) -> dict | None:
    row = db.one("SELECT user_id FROM integrations WHERE provider = 'telegram' AND json_extract(data, '$.chat_id') = ?", (chat_id,))
    return get_user(row["user_id"]) if row else None


def conversation_for(user: dict) -> int:
    row = db.one("SELECT id FROM conversations WHERE user_id = ? AND channel = 'telegram' ORDER BY id DESC LIMIT 1", (user["id"],))
    if row:
        return row["id"]
    return db.insert("conversations", user_id=user["id"], title="📱 Telegram", channel="telegram", created_at=now(), updated_at=now())


async def transcribe(audio: bytes) -> str:
    for url, key, model in (("https://api.groq.com/openai/v1/audio/transcriptions", os.environ.get("GROQ_API_KEY"), "whisper-large-v3-turbo"),
                            ("https://api.openai.com/v1/audio/transcriptions", os.environ.get("OPENAI_API_KEY"), "gpt-4o-mini-transcribe")):
        if key:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(url, headers={"Authorization": f"Bearer {key}"}, data={"model": model, "language": "fr"},
                                 files={"file": ("voice.ogg", audio, "audio/ogg")})
                if r.status_code < 400:
                    return r.json().get("text", "")
    return ""


async def handle(update: dict) -> None:
    from ..agent.runner import runner

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    if text.startswith("/start"):
        code = text.split(maxsplit=1)[1].strip() if " " in text else ""
        row = db.one("SELECT user_id FROM integrations WHERE provider = 'telegram_pending' AND json_extract(data, '$.code') = ?", (code,))
        if row:
            put(row["user_id"], "telegram", {"chat_id": chat_id, "username": msg["from"].get("username", "")})
            remove(row["user_id"], "telegram_pending")
            await telegram_send(chat_id, "✅ Compte Ely relié. Parle-moi ici comme dans l'application.")
        else:
            await telegram_send(chat_id, "Bonjour ! Pour me relier à ton compte Ely : Réglages → Connexions → Telegram.")
        return
    user = user_for_chat(chat_id)
    if not user:
        await telegram_send(chat_id, "Ce chat n'est pas relié à un compte Ely (Réglages → Connexions → Telegram).")
        return
    if msg.get("voice") or msg.get("audio"):
        audio = await download((msg.get("voice") or msg.get("audio"))["file_id"])
        text = await transcribe(audio) or text
        if not text:
            await telegram_send(chat_id, "Je n'ai pas pu transcrire ce vocal (clé GROQ ou OpenAI nécessaire).")
            return
    content: str | list = text
    if msg.get("photo"):
        from PIL import Image

        data = await download(msg["photo"][-1]["file_id"])
        img = Image.open(io.BytesIO(data))
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=82)
        content = [{"type": "text", "text": text or "(photo)"},
                   {"type": "image", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}]
    elif msg.get("document"):
        data = await download(msg["document"]["file_id"])
        name = msg["document"].get("file_name", "document")
        dest = settings.user_dir(user["id"]) / "files" / "Reçus" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        content = f"{text}\n\n[Fichier joint : Reçus/{name}]".strip()
    if not content:
        return
    await runner.submit(user, conversation_for(user), content, channel="telegram")


async def on_event(user_id: int, ev: dict) -> None:
    """Relaie vers Telegram les réponses et questions des conversations Telegram."""
    conv = db.one("SELECT channel FROM conversations WHERE id = ?", (ev.get("conversation_id"),))
    if not conv or conv["channel"] != "telegram":
        return
    chat_id = get(user_id, "telegram").get("chat_id")
    if not chat_id:
        return
    t = ev["type"]
    if t == "message":
        m = ev["message"]
        if m.get("role") == "assistant" and not m.get("tool_calls") and m.get("content") and m.get("kind") != "control":
            await telegram_send(chat_id, m["content"])
        if m.get("role") == "tool":
            for f in (m.get("files") or [])[:5]:
                path = settings.user_dir(user_id) / "files" / f
                if path.is_file() and path.stat().st_size < 45_000_000:
                    async with httpx.AsyncClient(timeout=120) as c:
                        await c.post(API.format(token=settings.telegram_bot_token, method="sendDocument"),
                                     data={"chat_id": chat_id}, files={"document": (path.name, path.read_bytes())})
    elif t == "ask_user":
        opts = "\n".join(f"• {o}" for o in ev.get("options") or [])
        await telegram_send(chat_id, f"❓ {ev['question']}\n{opts}".strip())
    elif t == "tool_start" and time.time() - _typing.get(chat_id, 0) > 5:
        _typing[chat_id] = time.time()
        await call("sendChatAction", chat_id=chat_id, action="typing")


async def polling_loop() -> None:
    global _username
    if not settings.telegram_bot_token:
        return
    from ..agent.runner import runner

    runner.listeners.append(on_event)
    try:
        me = await call("getMe")
        _username = me.get("result", {}).get("username", "")
    except Exception as e:
        log.warning("Telegram injoignable : %s", e)
    # un webhook laissé par une autre application (l'ancienne version d'Ely) bloquerait getUpdates
    try:
        await call("deleteWebhook")
    except Exception as e:
        log.info("Telegram : %s", e)
    offset = 0
    while True:
        try:
            res = await call("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "edited_message"])
            if not res.get("ok"):
                desc = res.get("description", "")
                log.warning("Telegram refuse getUpdates : %s", desc or res)
                if res.get("error_code") == 409 and "webhook" in desc.lower():
                    await call("deleteWebhook")
                await asyncio.sleep(RETRY_S)
                continue
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                asyncio.create_task(handle(upd))
        except Exception as e:
            log.info("Telegram : %s", e)
            await asyncio.sleep(RETRY_S)
