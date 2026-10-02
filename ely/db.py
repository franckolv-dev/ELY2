"""Base de données SQLite (un seul fichier, zéro service à faire tourner).

Accès synchrone protégé par un verrou : les requêtes prennent quelques
microsecondes, inutile d'ajouter une couche asynchrone.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    settings TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    user_agent TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    created_by INTEGER,
    created_at REAL NOT NULL,
    used_by INTEGER
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Nouvelle conversation',
    model TEXT DEFAULT '',
    channel TEXT DEFAULT 'web',
    pinned INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    summary_upto INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    run_id INTEGER,
    role TEXT NOT NULL,
    data TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_msg_run ON messages(run_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, conversation_id UNINDEXED, user_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    objective TEXT NOT NULL,
    model TEXT DEFAULT '',
    steps INTEGER DEFAULT 0,
    state TEXT NOT NULL DEFAULT '{}',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'fait',
    source TEXT DEFAULT 'auto',
    embedding BLOB,
    embed_model TEXT DEFAULT '',
    uses INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    content TEXT NOT NULL,
    uses INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(name, description, content, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    company TEXT DEFAULT '',
    address TEXT DEFAULT '',
    birthday TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    all_day INTEGER DEFAULT 0,
    location TEXT DEFAULT '',
    description TEXT DEFAULT '',
    reminder_minutes INTEGER DEFAULT 30,
    reminded INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, start);
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service TEXT NOT NULL,
    url TEXT DEFAULT '',
    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS integrations (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id INTEGER,
    instruction TEXT NOT NULL,
    cron TEXT DEFAULT '',
    next_run REAL,
    enabled INTEGER DEFAULT 1,
    last_run REAL,
    last_status TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    purpose TEXT DEFAULT 'agent',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    user_id INTEGER,
    name TEXT NOT NULL,
    ok INTEGER NOT NULL,
    ms INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    args TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toollog_time ON tool_log(created_at);
CREATE INDEX IF NOT EXISTS idx_toollog_run ON tool_log(run_id);
CREATE TABLE IF NOT EXISTS improvements (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    diff TEXT DEFAULT '',
    commit_sha TEXT DEFAULT '',
    status TEXT DEFAULT 'applied',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        with self.lock:
            self.conn.executescript(SCHEMA)

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self.lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
            return dict(r) if r else None

    def val(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self.lock:
            r = self.conn.execute(sql, tuple(params)).fetchone()
            return r[0] if r else None

    def run(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Exécute une écriture ; renvoie lastrowid (INSERT) ou rowcount."""
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    def insert(self, table: str, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self.run(f"INSERT INTO {table} ({cols}) VALUES ({marks})", fields.values())

    def update(self, table: str, where: str, where_params: Iterable[Any], **fields: Any) -> int:
        sets = ", ".join(f"{k} = ?" for k in fields)
        return self.run(f"UPDATE {table} SET {sets} WHERE {where}", [*fields.values(), *where_params])

    # Réglages globaux (modifiables depuis l'interface d'administration)
    def get_setting(self, key: str, default: Any = None) -> Any:
        v = self.val("SELECT value FROM app_settings WHERE key = ?", (key,))
        return json.loads(v) if v is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        self.run(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def fts_query(text: str) -> str:
    """Transforme un texte libre en requête FTS5 tolérante (OU entre les mots utiles)."""
    import re

    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]
    stop = {
        "les", "des", "une", "pour", "que", "qui", "dans", "sur", "avec", "est", "pas", "mon", "mes",
        "ton", "tes", "son", "ses", "the", "and", "for", "you", "moi", "toi", "lui", "elle", "nous",
        "vous", "ils", "elles", "leur", "cette", "ces", "aux", "par", "plus", "tout", "fait", "faire",
    }
    words = [w for w in words if w not in stop][:12]
    return " OR ".join(f'"{w}"*' for w in words) if words else ""


def now() -> float:
    return time.time()


db = DB(str(settings.db_path))
