"""Routes : authentification, conversations, flux temps réel, fichiers, navigateur, voix."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import CODE_VERSION, auth
from ..agent.runner import public_message, runner
from ..browser import manager
from ..config import settings
from ..db import db, now

router = APIRouter()


# ---------------------------------------------------------------------- comptes
class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""
    invite: str = ""


def _login_response(user: dict, request: Request, response: Response) -> dict:
    token = auth.create_session(user["id"], request.headers.get("user-agent", ""))
    response.set_cookie("ely_token", token, max_age=365 * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return {"token": token, "user": user}


@router.get("/api/setup")
def setup_state():
    return {"needs_setup": not db.val("SELECT COUNT(*) FROM users"), "open_registration": settings.open_registration,
            "version": CODE_VERSION}


@router.post("/api/auth/register")
def register(body: Credentials, request: Request, response: Response):
    first = not db.val("SELECT COUNT(*) FROM users")
    if not first and not settings.open_registration:
        inv = db.one("SELECT code FROM invites WHERE code = ? AND used_by IS NULL", (body.invite.strip(),))
        if not inv:
            raise HTTPException(403, "Inscription sur invitation : demandez un code à l'administrateur")
    user = auth.create_user(body.email, body.name, body.password)
    if not first and body.invite:
        db.run("UPDATE invites SET used_by = ? WHERE code = ?", (user["id"], body.invite.strip()))
    return _login_response(user, request, response)


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    row = db.one("SELECT id, password_hash FROM users WHERE email = ?", (body.email.strip().lower(),))
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        time.sleep(0.5)
        raise HTTPException(401, "E-mail ou mot de passe incorrect")
    return _login_response(auth.get_user(row["id"]), request, response)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = auth.token_from_request(request)
    if token:
        db.run("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie("ely_token")
    return {"ok": True}


@router.get("/api/me")
def me(user=Depends(auth.current_user)):
    return user


class MeUpdate(BaseModel):
    name: str | None = None
    password: str | None = None
    current_password: str | None = None
    settings: dict | None = None


@router.patch("/api/me")
def update_me(body: MeUpdate, user=Depends(auth.current_user)):
    if body.name:
        db.run("UPDATE users SET name = ? WHERE id = ?", (body.name.strip(), user["id"]))
    if body.password:
        row = db.one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
        if not auth.verify_password(body.current_password or "", row["password_hash"]):
            raise HTTPException(403, "Mot de passe actuel incorrect")
        db.run("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(body.password), user["id"]))
    if body.settings is not None:
        auth.update_user_settings(user["id"], **body.settings)
    return auth.get_user(user["id"])


# ---------------------------------------------------------------------- conversations
def _own(conv_id: int, user: dict) -> dict:
    c = db.one("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user["id"]))
    if not c:
        raise HTTPException(404, "Conversation introuvable")
    return c


@router.get("/api/conversations")
def list_conversations(q: str = "", user=Depends(auth.current_user)):
    if q:
        from ..memory.store import search_history

        ids = {h["conversation_id"] for h in search_history(user["id"], q, 50)}
        rows = db.all("SELECT id, title, channel, pinned, model, updated_at FROM conversations WHERE user_id = ? AND "
                      "(title LIKE ? OR id IN (" + ",".join(str(i) for i in ids or [0]) + ")) ORDER BY updated_at DESC",
                      (user["id"], f"%{q}%"))
    else:
        rows = db.all("SELECT id, title, channel, pinned, model, updated_at FROM conversations WHERE user_id = ? "
                      "ORDER BY pinned DESC, updated_at DESC LIMIT 300", (user["id"],))
    for r in rows:
        st = runner.states.get(r["id"])
        r["status"] = st.status if st else "idle"
    return rows


class ConvCreate(BaseModel):
    title: str = "Nouvelle conversation"
    model: str = ""


@router.post("/api/conversations")
def create_conversation(body: ConvCreate, user=Depends(auth.current_user)):
    cid = db.insert("conversations", user_id=user["id"], title=body.title or "Nouvelle conversation", model=body.model,
                    created_at=now(), updated_at=now())
    return db.one("SELECT * FROM conversations WHERE id = ?", (cid,))


class ConvUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    model: str | None = None


@router.patch("/api/conversations/{cid}")
def update_conversation(cid: int, body: ConvUpdate, user=Depends(auth.current_user)):
    _own(cid, user)
    fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in body.model_dump().items() if v is not None}
    if fields:
        db.update("conversations", "id = ?", (cid,), **fields)
    return db.one("SELECT * FROM conversations WHERE id = ?", (cid,))


@router.delete("/api/conversations/{cid}")
async def delete_conversation(cid: int, user=Depends(auth.current_user)):
    _own(cid, user)
    await runner.cancel(cid)
    db.run("DELETE FROM messages_fts WHERE conversation_id = ?", (cid,))
    db.run("DELETE FROM conversations WHERE id = ?", (cid,))
    runner.states.pop(cid, None)
    return {"ok": True}


@router.get("/api/conversations/{cid}/messages")
def get_messages(cid: int, user=Depends(auth.current_user)):
    conv = _own(cid, user)
    rows = db.all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))
    st = runner.states.get(cid)
    return {"conversation": conv, "messages": [public_message(r) for r in rows], "state": st.snapshot() if st else None}


class ChatIn(BaseModel):
    text: str = ""
    conversation_id: int | None = None
    attachments: list[str] = []
    model: str | None = None


def build_content(user: dict, text: str, attachments: list[str]):
    """Texte + pièces jointes : les images vont directement au modèle, les documents sont signalés à l'agent."""
    if not attachments:
        return text
    root = settings.user_dir(user["id"]) / "files"
    parts: list[dict] = []
    notes = []
    for a in attachments:
        p = (root / a).resolve()
        if not p.is_relative_to(root.resolve()) or not p.exists():
            continue
        ctype = mimetypes.guess_type(p.name)[0] or ""
        if ctype.startswith("image/"):
            from PIL import Image

            img = Image.open(p)
            img.thumbnail((1600, 1600))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=82)
            parts.append({"type": "image", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode(),
                          "src": f"/files/{a}"})
        notes.append(f"[Fichier joint : {a}]")
    parts.insert(0, {"type": "text", "text": (text + "\n\n" if text else "") + "\n".join(notes)})
    return parts


@router.post("/api/chat")
async def chat(body: ChatIn, user=Depends(auth.current_user)):
    if not body.text.strip() and not body.attachments:
        raise HTTPException(400, "Message vide")
    cid = body.conversation_id
    if cid:
        _own(cid, user)
    else:
        cid = db.insert("conversations", user_id=user["id"], title="Nouvelle conversation", model=body.model or "",
                        created_at=now(), updated_at=now())
    if body.model is not None and body.conversation_id:
        db.update("conversations", "id = ?", (cid,), model=body.model)
    res = await runner.submit(user, cid, build_content(user, body.text.strip(), body.attachments))
    return {"conversation_id": cid, **res}


@router.post("/api/conversations/{cid}/cancel")
async def cancel(cid: int, user=Depends(auth.current_user)):
    _own(cid, user)
    return {"cancelled": await runner.cancel(cid)}


@router.get("/api/events")
async def events(request: Request, user=Depends(auth.current_user)):
    q = runner.subscribe(user["id"])

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'states': runner.snapshots(user['id'])}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            runner.unsubscribe(user["id"], q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ---------------------------------------------------------------------- navigateur en direct
@router.get("/api/browser/frame")
async def browser_frame(conversation_id: int | None = None, fresh: bool = False, user=Depends(auth.current_user)):
    ub = manager.current(user["id"])

    async def live():
        page = await ub.page(ub.active_key)
        try:
            return {"image": await ub.frame(page), "url": page.url}
        except Exception:  # Chrome peut refuser la capture d'une fenêtre masquée
            return {"image": None, "url": page.url}

    if ub and (fresh or not conversation_id):
        return await live()
    st = runner.states.get(conversation_id or 0)
    if st and st.last_frame and st.user_id == user["id"]:
        return st.last_frame
    if ub:
        return await live()
    return {"image": None, "url": ""}


class BrowserAction(BaseModel):
    action: str  # click | type | key | scroll | goto | back
    x: float = 0
    y: float = 0
    text: str = ""


@router.post("/api/browser/action")
async def browser_action(body: BrowserAction, user=Depends(auth.current_user)):
    """Prise de main : l'utilisateur agit lui-même dans le navigateur d'Ely (connexion, captcha…)."""
    ub = await manager.for_user(user["id"])
    page = await ub.page(ub.active_key)
    vp = page.viewport_size or {"width": 1280, "height": 860}
    if body.action == "click":
        await page.mouse.click(body.x * vp["width"], body.y * vp["height"])
    elif body.action == "type":
        await page.keyboard.type(body.text, delay=20)
    elif body.action == "key":
        await page.keyboard.press(body.text or "Enter")
    elif body.action == "scroll":
        await page.mouse.wheel(0, 600 if body.y >= 0 else -600)
    elif body.action == "goto":
        url = body.text if body.text.startswith("http") else f"https://{body.text}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    elif body.action == "back":
        await page.go_back()
    await asyncio.sleep(0.8)
    try:
        return {"image": await ub.frame(page), "url": page.url}
    except Exception:
        return {"image": None, "url": page.url}


# ---------------------------------------------------------------------- fichiers
def _ws(user: dict) -> Path:
    return (settings.user_dir(user["id"]) / "files").resolve()


def _safe(user: dict, path: str) -> Path:
    root = _ws(user)
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise HTTPException(400, "Chemin invalide")
    return p


@router.get("/api/files")
def list_files(user=Depends(auth.current_user)):
    root = _ws(user)
    out = []
    for p in sorted(root.rglob("*"), key=lambda p: -p.stat().st_mtime):
        rel = p.relative_to(root)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            out.append({"path": str(rel), "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                        "type": mimetypes.guess_type(p.name)[0] or ""})
    return out[:1000]


@router.post("/api/files/upload")
async def upload(file: UploadFile = File(...), user=Depends(auth.current_user)):
    name = re.sub(r"[^\w.\- ()àâäéèêëîïôöùûüç]", "_", Path(file.filename or "fichier").name)[:120]
    dest = _ws(user) / "Reçus" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-{int(time.time())}{dest.suffix}")
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return {"path": str(dest.relative_to(_ws(user))), "size": dest.stat().st_size}


@router.delete("/api/files")
def delete_file(path: str, user=Depends(auth.current_user)):
    p = _safe(user, path)
    if p.is_file():
        p.unlink()
    return {"ok": True}


@router.get("/files/{path:path}")
def serve_file(path: str, download: bool = False, user=Depends(auth.current_user)):
    p = _safe(user, path)
    if not p.is_file():
        raise HTTPException(404, "Fichier introuvable")
    return FileResponse(p, filename=p.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


# ---------------------------------------------------------------------- voix
@router.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), user=Depends(auth.current_user)):
    import os

    audio = await file.read()
    for url, key, model in (("https://api.groq.com/openai/v1/audio/transcriptions", os.environ.get("GROQ_API_KEY"), "whisper-large-v3-turbo"),
                            ("https://api.openai.com/v1/audio/transcriptions", os.environ.get("OPENAI_API_KEY"), "gpt-4o-mini-transcribe")):
        if not key:
            continue
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(url, headers={"Authorization": f"Bearer {key}"}, data={"model": model, "language": "fr"},
                             files={"file": (file.filename or "audio.webm", audio, file.content_type or "audio/webm")})
        if r.status_code < 400:
            return {"text": r.json().get("text", "")}
    raise HTTPException(501, "Aucun service de transcription configuré (GROQ_API_KEY ou OPENAI_API_KEY)")
