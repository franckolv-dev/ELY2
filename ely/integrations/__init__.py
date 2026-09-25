"""Connexions aux services externes, stockées par utilisateur."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..config import settings
from ..db import db, now


def get(user_id: int, provider: str) -> dict:
    row = db.one("SELECT data FROM integrations WHERE user_id = ? AND provider = ?", (user_id, provider))
    return json.loads(row["data"]) if row else {}


def put(user_id: int, provider: str, data: dict) -> None:
    db.run(
        "INSERT INTO integrations(user_id, provider, data, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id, provider) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (user_id, provider, json.dumps(data), now()),
    )


def remove(user_id: int, provider: str) -> None:
    db.run("DELETE FROM integrations WHERE user_id = ? AND provider = ?", (user_id, provider))


def connected(user_id: int, provider: str) -> bool:
    return bool(get(user_id, provider))


def sign_state(user_id: int, provider: str) -> str:
    """État OAuth signé (évite de stocker quoi que ce soit côté serveur)."""
    payload = f"{user_id}:{provider}:{int(time.time())}"
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def verify_state(state: str, provider: str, max_age: int = 1800) -> int | None:
    try:
        uid, prov, ts, sig = base64.urlsafe_b64decode(state.encode()).decode().split(":")
        payload = f"{uid}:{prov}:{ts}"
        good = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:24]
        if hmac.compare_digest(sig, good) and prov == provider and time.time() - int(ts) < max_age:
            return int(uid)
    except Exception:
        pass
    return None
