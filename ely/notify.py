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


def _push_sync(sub: dict, payload: dict, keys: dict) -> int:
    from pywebpush import WebPushException, webpush

    try:
        webpush(subscription_info=sub, data=json.dumps(payload), vapid_private_key=keys["private_pem"],
                vapid_claims={"sub": settings.vapid_contact}, ttl=86400)
        return 201
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0
    except Exception as e:
        log.info("push impossible : %s", e)
        return 0


async def telegram_send(chat_id: int | str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=20) as c:
        for i in range(0, len(text), 4000):
            await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                         json={"chat_id": chat_id, "text": text[i:i + 4000]})


async def notify(user_id: int, title: str, body: str, url: str = "/", tag: str = "") -> int:
    """Envoie une notification sur tous les appareils de l'utilisateur. Renvoie le nombre d'envois réussis."""
    sent = 0
    subs = db.all("SELECT endpoint, data FROM push_subscriptions WHERE user_id = ?", (user_id,))
    if subs:
        keys = vapid_keys()
        payload = {"title": title, "body": body[:400], "url": url, "tag": tag or url}
        for s in subs:
            status = await asyncio.to_thread(_push_sync, json.loads(s["data"]), payload, keys)
            if status in (404, 410):  # abonnement expiré
                db.run("DELETE FROM push_subscriptions WHERE endpoint = ?", (s["endpoint"],))
            elif 200 <= status < 300:
                sent += 1
    tg = get(user_id, "telegram")
    if tg.get("chat_id"):
        try:
            await telegram_send(tg["chat_id"], f"{title}\n\n{body}")
            sent += 1
        except Exception as e:
            log.info("telegram : %s", e)
    return sent
