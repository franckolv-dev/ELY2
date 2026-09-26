"""Comptes utilisateurs et sessions (jetons opaques stockés en base)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from fastapi import Depends, HTTPException, Request

from .db import db, now

SESSION_DAYS = 365


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h, base64.b64decode(hash_b64))
    except Exception:
        return False


def create_user(email: str, name: str, password: str, role: str | None = None) -> dict:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Adresse e-mail invalide")
    if len(password) < 6:
        raise HTTPException(400, "Mot de passe trop court (6 caractères minimum)")
    if db.one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "Un compte existe déjà avec cette adresse")
    if role is None:
        role = "admin" if not db.val("SELECT COUNT(*) FROM users") else "user"
    uid = db.insert(
        "users", email=email, name=name.strip() or email.split("@")[0], password_hash=hash_password(password),
        role=role, settings="{}", created_at=now(),
    )
    return get_user(uid)


def get_user(uid: int) -> dict | None:
    u = db.one("SELECT id, email, name, role, settings, created_at FROM users WHERE id = ?", (uid,))
    if u:
        u["settings"] = json.loads(u["settings"] or "{}")
    return u


def create_session(user_id: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    db.insert("sessions", token=token, user_id=user_id, created_at=now(), last_seen=now(), user_agent=user_agent[:200])
    return token


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    s = db.one("SELECT user_id, last_seen FROM sessions WHERE token = ?", (token,))
    if not s or now() - s["last_seen"] > SESSION_DAYS * 86400:
        return None
    if now() - s["last_seen"] > 3600:
        db.run("UPDATE sessions SET last_seen = ? WHERE token = ?", (now(), token))
    return get_user(s["user_id"])


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get("ely_token") or request.query_params.get("token")


def current_user(request: Request) -> dict:
    user = user_from_token(token_from_request(request))
    if not user:
        raise HTTPException(401, "Non connecté")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Réservé à l'administrateur")
    return user


def addresses_informally(user: dict | None) -> bool:
    """Vouvoiement par défaut ; tutoiement seulement si la personne l'a demandé."""
    return ((user or {}).get("settings") or {}).get("address") == "tu"


def tv(user: dict | None, vous: str, tu: str) -> str:
    return tu if addresses_informally(user) else vous


def update_user_settings(user_id: int, **values) -> dict:
    u = get_user(user_id)
    s = {**u["settings"], **values}
    db.run("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(s), user_id))
    return s
