"""Notifications : push web (téléphone Android, ordinateur) et Telegram."""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpx

from .config import settings
from .db import db, now
from .integrations import get

log = logging.getLogger("ely.notify")


def vapid_keys() -> dict:
    path = settings.data_dir / "vapid.json"
    if path.exists():
        return json.loads(path.read_text())
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    data = {"private_pem": pem, "public": base64.urlsafe_b64encode(pub).decode().rstrip("=")}
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return data


def save_subscription(user_id: int, sub: dict) -> None:
    db.run("INSERT INTO push_subscriptions(endpoint, user_id, data, created_at) VALUES(?,?,?,?) "
           "ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, data = excluded.data",
           (sub["endpoint"], user_id, json.dumps(sub), now()))


PUSH_TIMEOUT = 10  # s : un service de notification muet ne bloque jamais le planificateur


def _push_sync(sub: dict, payload: dict, keys: dict) -> int:
    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

    try:
        # la clé est en PEM : passée en texte, pywebpush la lit comme du base64 et échoue (aucun push n'arrivait)
        webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=Vapid.from_pem(keys["private_pem"].encode()),
                vapid_claims={"sub": settings.vapid_contact}, ttl=86400, timeout=PUSH_TIMEOUT)
        return 201
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0
    except Exception as e:
        log.warning("push impossible : %s", e)
        return 0


def telegram_chunks(text: str) -> list[str]:
    """Conserve tout le texte, avec une marge sous 4096 unités UTF-16 (emoji compris)."""
    chunks, start, units = [], 0, 0
    for i, char in enumerate(text):
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > 4000:
            chunks.append(text[start:i])
            start, units = i, 0
        units += size
    if text[start:]:
        chunks.append(text[start:])
    return chunks


class TelegramSendError(RuntimeError):
    """Erreur sûre à journaliser ; conserve les accusés des parties déjà acceptées."""

    def __init__(self, reason: str, message_ids: list[int], total: int):
        super().__init__(reason)
        self.message_ids = list(message_ids)
        self.total = total


async def telegram_send(chat_id: int | str, text: str) -> list[int]:
    chunks = telegram_chunks(text)
    ids: list[int] = []
    if not settings.telegram_bot_token:
        raise TelegramSendError("bot non configuré", ids, len(chunks))
    if not chunks:
        raise TelegramSendError("texte vide", ids, 0)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            for chunk in chunks:
                r = await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                                 json={"chat_id": chat_id, "text": chunk})
                # Ne jamais exposer l'URL de l'exception HTTP : elle contient le jeton du bot.
                if not 200 <= r.status_code < 300:
                    raise TelegramSendError(f"HTTP {r.status_code}", ids, len(chunks))
                data = r.json()
                result = data.get("result") if isinstance(data, dict) else None
                mid = result.get("message_id") if isinstance(result, dict) else None
                if not isinstance(data, dict) or data.get("ok") is not True:
                    raise TelegramSendError("refus de l'API Telegram", ids, len(chunks))
                if type(mid) is not int or mid <= 0:
                    raise TelegramSendError("accusé Telegram invalide", ids, len(chunks))
                ids.append(mid)
    except TelegramSendError:
        raise
    except Exception:
        # Timeout/connexion coupée : la partie en cours peut avoir été acceptée. Pas de renvoi automatique.
        raise TelegramSendError("réponse Telegram non confirmée (transport ou format)", ids, len(chunks)) from None
    return ids


async def notify(user_id: int, title: str, body: str, url: str = "/", tag: str = "",
                 receipt: dict | None = None) -> int:
    """Nombre de destinations acceptées ; détail facultatif sans changer le contrat des appelants existants."""
    receipt = receipt if receipt is not None else {}
    receipt.update(push_accepted=0, push_total=0, telegram_status="not_linked", telegram_ids=[], telegram_total=0)
    sent = 0
    subs = db.all("SELECT endpoint, data FROM push_subscriptions WHERE user_id = ?", (user_id,))
    receipt["push_total"] = len(subs)
    if subs:
        keys = vapid_keys()
        payload = {"title": title, "body": body[:400], "url": url, "tag": tag or url}
        for s in subs:
            status = await asyncio.to_thread(_push_sync, json.loads(s["data"]), payload, keys)
            if status in (404, 410):  # abonnement expiré
                db.run("DELETE FROM push_subscriptions WHERE endpoint = ?", (s["endpoint"],))
            elif 200 <= status < 300:
                sent += 1
    receipt["push_accepted"] = sent
    tg = get(user_id, "telegram")
    if tg.get("chat_id"):
        text = f"{title}\n\n{body}"
        receipt.update(telegram_status="unconfirmed", telegram_total=len(telegram_chunks(text)))
        try:
            ids = await telegram_send(tg["chat_id"], text)
            if (isinstance(ids, list) and len(ids) == receipt["telegram_total"]
                    and all(type(mid) is int and mid > 0 for mid in ids)):
                receipt.update(telegram_status="accepted", telegram_ids=ids)
                sent += 1
        except TelegramSendError as e:
            receipt.update(telegram_status="partial" if e.message_ids else "unconfirmed",
                           telegram_ids=e.message_ids, telegram_total=e.total, telegram_error=str(e))
            log.info("telegram : %s (%s/%s parties confirmées)", e, len(e.message_ids), e.total)
        except Exception:
            # Même un transport remplacé par un plugin ne doit pas divulguer le jeton dans ses erreurs.
            receipt["telegram_error"] = "envoi non confirmé"
            log.info("telegram : envoi non confirmé")
    return sent
