"""Sauvegardes de la base : copies cohérentes faites par SQLite lui-même (sûres pendant qu'Ely écrit), gardées dans
data/sauvegardes/. Une par jour, et une avant chaque activation d'une modification du code par Ely : un retour
arrière du code ne touche pas aux données, la copie d'avant permet de les retrouver (voir le README).
Les secrets y restent chiffrés : la restauration demande aussi data/secret.key (ou ELY_SECRET_KEY)."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from .config import settings
from .db import db

log = logging.getLogger("ely.backup")

KEEP = {"quotidienne": 7, "avant-mise-a-jour": 5}  # copies gardées de chaque sorte
MARGIN = 1 << 30  # jamais une copie qui ne laisserait pas au moins 1 Go libre


def folder() -> Path:
    return settings.data_dir / "sauvegardes"


def backup(kind: str = "quotidienne") -> Path:
    """Copie complète de la base ; lève OSError s'il n'y a pas la place."""
    out = folder()
    out.mkdir(parents=True, exist_ok=True)
    size = settings.db_path.stat().st_size + sum(p.stat().st_size for p in settings.db_path.parent.glob("ely.db-wal"))
    if shutil.disk_usage(out).free < size + MARGIN:
        raise OSError("pas assez de place sur le disque pour sauvegarder la base")
    dest = out / f"ely-{time.strftime('%Y-%m-%d_%H%M%S')}-{kind}.db"
    tmp = dest.with_name(dest.name + ".partiel")
    # connexion à part : en WAL, la lecture ne bloque ni Ely ni ses écritures, et la copie est cohérente
    src, target = sqlite3.connect(settings.db_path), sqlite3.connect(tmp)
    try:
        src.backup(target)
    finally:
        target.close()
        src.close()
    tmp.chmod(0o600)
    tmp.replace(dest)
    for old in sorted(out.glob(f"ely-*-{kind}.db"), reverse=True)[KEEP.get(kind, 5):]:
        old.unlink(missing_ok=True)
    return dest


def latest(kind: str) -> Path | None:
    found = sorted(folder().glob(f"ely-*-{kind}.db"))
    return found[-1] if found else None


async def daily() -> None:
    """Une copie par jour, au premier passage du planificateur de la journée."""
    import asyncio

    today = dt.date.today().isoformat()
    if db.get_setting("backup_last_day", "") == today:
        return
    db.set_setting("backup_last_day", today)
    try:
        path = await asyncio.to_thread(backup, "quotidienne")
        log.info("base sauvegardée : %s", path.name)
    except Exception as e:
        log.warning("sauvegarde de la base impossible : %s", e)
