"""Chiffrement des secrets gardés en base : mots de passe du coffre, jetons et mots de passe des connexions.

La clé dérive de la clé secrète d'Ely (data/secret.key ou ELY_SECRET_KEY) : une copie de la base seule ne livre
aucun secret. Une valeur sans préfixe est une ancienne valeur en clair, lue telle quelle puis chiffrée au démarrage.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings
from .db import db

log = logging.getLogger("ely.vault")

PREFIX = "ely1:"


def _fernet() -> Fernet:
    key = hashlib.sha256(b"ely-vault:" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(text: str) -> str:
    if not text or text.startswith(PREFIX):
        return text
    return PREFIX + _fernet().encrypt(text.encode()).decode()


def unseal(text: str) -> str:
    """Valeur en clair ; vide si elle a été chiffrée avec une autre clé secrète (à ressaisir)."""
    if not text or not text.startswith(PREFIX):
        return text or ""
    try:
        return _fernet().decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("secret illisible : la clé secrète d'Ely a changé depuis son enregistrement")
        return ""


def passwords(user_id: int) -> set[str]:
    """Mots de passe du coffre de la personne, pour les masquer dans les journaux."""
    rows = db.all("SELECT password FROM credentials WHERE user_id = ? AND password != ''", (user_id,))
    return {p for p in (unseal(r["password"]) for r in rows) if len(p) >= 4}


def clear_provider(provider: str) -> bool:
    """Telegram reste en clair : il est retrouvé par son numéro de discussion (json_extract), sans secret."""
    return provider.startswith("telegram")


def migrate() -> int:
    """Chiffre les secrets encore en clair (bases antérieures) ; renvoie le nombre de valeurs chiffrées."""
    n = 0
    for r in db.all("SELECT id, password FROM credentials WHERE password != '' AND password NOT LIKE ?", (PREFIX + "%",)):
        n += db.run("UPDATE credentials SET password = ? WHERE id = ?", (seal(r["password"]), r["id"]))
    for r in db.all("SELECT user_id, provider, data FROM integrations WHERE data NOT LIKE ?", (PREFIX + "%",)):
        if not clear_provider(r["provider"]):
            n += db.run("UPDATE integrations SET data = ? WHERE user_id = ? AND provider = ?",
                        (seal(r["data"]), r["user_id"], r["provider"]))
    return n
