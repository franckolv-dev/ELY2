"""Mémoire d'Ely.

- Profil : un document court et vivant par utilisateur, toujours dans le contexte.
- Souvenirs : faits précis, retrouvés par recherche hybride (plein texte + vecteurs, fusion RRF).
- Historique : recherche plein texte dans toutes les conversations passées.
- Compétences : procédures apprises, injectées automatiquement quand elles sont pertinentes.
"""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from ..db import db, fts_query, now
from ..llm import registry

log = logging.getLogger("ely.memory")


# ---------------------------------------------------------------------- profil
def get_profile(user_id: int) -> str:
    return db.val("SELECT content FROM profiles WHERE user_id = ?", (user_id,)) or ""


def set_profile(user_id: int, content: str) -> None:
    db.run("INSERT INTO profiles(user_id, content, updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE "
           "SET content = excluded.content, updated_at = excluded.updated_at", (user_id, content.strip(), now()))


# ---------------------------------------------------------------------- vecteurs
def _to_blob(v: list[float]) -> bytes:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return (a / n if n else a).tobytes()


async def embed_one(text: str) -> tuple[str, bytes] | None:
    res = await registry.embed([text[:2000]])
    if not res:
        return None
    model, vecs = res
    return model, _to_blob(vecs[0])


# ---------------------------------------------------------------------- souvenirs
async def add_memory(user_id: int, content: str, category: str = "fait", source: str = "auto") -> tuple[int, bool]:
    """Ajoute un souvenir ; s'il existe déjà un souvenir quasi identique, le remplace. Renvoie (id, nouveau)."""
    content = content.strip()
    emb = await embed_one(content)
    dup = None
    if emb:
        dup = _nearest(user_id, emb, threshold=0.92)
    if dup is None:
        q = fts_query(content)
        if q:
            row = db.one("SELECT m.id, m.content FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                         "WHERE memories_fts MATCH ? AND m.user_id = ? ORDER BY bm25(memories_fts) LIMIT 1", (q, user_id))
            if row and _jaccard(row["content"], content) > 0.75:
                dup = row["id"]
    if dup is not None:
        db.update("memories", "id = ?", (dup,), content=content, category=category, updated_at=now(),
                  embedding=emb[1] if emb else None, embed_model=emb[0] if emb else "")
        db.run("DELETE FROM memories_fts WHERE rowid = ?", (dup,))
        db.run("INSERT INTO memories_fts(rowid, content) VALUES(?, ?)", (dup, content))
        return dup, False
    mid = db.insert("memories", user_id=user_id, content=content, category=category, source=source,
                    embedding=emb[1] if emb else None, embed_model=emb[0] if emb else "", created_at=now(), updated_at=now())
    db.run("INSERT OR REPLACE INTO memories_fts(rowid, content) VALUES(?, ?)", (mid, content))
    return mid, True


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.lower().split()), set(b.lower().split())
    return len(sa & sb) / max(1, len(sa | sb))


def _nearest(user_id: int, emb: tuple[str, bytes], threshold: float) -> int | None:
    model, blob = emb
    rows = db.all("SELECT id, embedding FROM memories WHERE user_id = ? AND embed_model = ? AND embedding IS NOT NULL", (user_id, model))
    if not rows:
        return None
    q = np.frombuffer(blob, dtype=np.float32)
    mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    if mat.shape[1] != q.shape[0]:
        return None
    sims = mat @ q
    i = int(np.argmax(sims))
    return rows[i]["id"] if sims[i] >= threshold else None


def delete_memory(user_id: int, mid: int) -> bool:
    n = db.run("DELETE FROM memories WHERE id = ? AND user_id = ?", (mid, user_id))
    if n:
        db.run("DELETE FROM memories_fts WHERE rowid = ?", (mid,))
    return bool(n)


def list_memories(user_id: int, limit: int = 500) -> list[dict]:
    return db.all("SELECT id, content, category, source, uses, created_at, updated_at FROM memories WHERE user_id = ? "
                  "ORDER BY updated_at DESC LIMIT ?", (user_id, limit))


async def search_memories(user_id: int, query: str, k: int = 8) -> list[dict]:
    """Recherche hybride : plein texte (BM25) + similarité vectorielle, fusionnés par rang (RRF)."""
    ranks: dict[int, float] = {}
    q = fts_query(query)
    if q:
        rows = db.all("SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid WHERE memories_fts MATCH ? "
                      "AND m.user_id = ? ORDER BY bm25(memories_fts) LIMIT 20", (q, user_id))
        for i, r in enumerate(rows):
            ranks[r["id"]] = ranks.get(r["id"], 0) + 1 / (60 + i)
    emb = await embed_one(query)
    if emb:
        model, blob = emb
        rows = db.all("SELECT id, embedding FROM memories WHERE user_id = ? AND embed_model = ? AND embedding IS NOT NULL",
                      (user_id, model))
        if rows:
            qv = np.frombuffer(blob, dtype=np.float32)
            mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            if mat.shape[1] == qv.shape[0]:
                sims = mat @ qv
                order = np.argsort(-sims)[:20]
                for i, idx in enumerate(order):
                    if sims[idx] < 0.25:
                        break
                    rid = rows[int(idx)]["id"]
                    ranks[rid] = ranks.get(rid, 0) + 1 / (60 + i)
    if not ranks:
        return []
    best = sorted(ranks, key=lambda i: -ranks[i])[:k]
    rows = {r["id"]: r for r in db.all(f"SELECT id, content, category, updated_at FROM memories WHERE id IN ({','.join('?' * len(best))})", best)}
    db.run(f"UPDATE memories SET uses = uses + 1 WHERE id IN ({','.join('?' * len(best))})", best)
    return [rows[i] for i in best if i in rows]


async def backfill_embeddings(limit: int = 200) -> int:
    """Calcule les vecteurs manquants (ou d'un ancien modèle) en tâche de fond."""
    model = registry.resolve("embed")
    if not model:
        return 0
    rows = db.all("SELECT id, content FROM memories WHERE embed_model != ? OR embedding IS NULL LIMIT ?", (model, limit))
    done = 0
    for i in range(0, len(rows), 32):
        batch = rows[i: i + 32]
        res = await registry.embed([r["content"][:2000] for r in batch])
        if not res:
            break
        m, vecs = res
        for r, v in zip(batch, vecs):
            db.update("memories", "id = ?", (r["id"],), embedding=_to_blob(v), embed_model=m)
            done += 1
    return done


# ---------------------------------------------------------------------- historique
def index_message(message_id: int, conversation_id: int, user_id: int, text: str) -> None:
    if text and text.strip():
        db.run("INSERT OR REPLACE INTO messages_fts(rowid, text, conversation_id, user_id) VALUES(?,?,?,?)",
               (message_id, text[:20000], conversation_id, user_id))


def search_history(user_id: int, query: str, limit: int = 10, exclude_conversation: int | None = None) -> list[dict]:
    q = fts_query(query)
    if not q:
        return []
    rows = db.all(
        "SELECT f.rowid AS id, f.conversation_id, snippet(messages_fts, 0, '«', '»', '…', 24) AS extract, "
        "c.title, m.created_at, m.role FROM messages_fts f JOIN messages m ON m.id = f.rowid "
        "JOIN conversations c ON c.id = f.conversation_id WHERE messages_fts MATCH ? AND f.user_id = ? "
        "ORDER BY bm25(messages_fts) LIMIT ?", (q, user_id, limit * 2))
    out = [r for r in rows if r["conversation_id"] != exclude_conversation]
    return out[:limit]


# ---------------------------------------------------------------------- compétences
def save_skill(user_id: int | None, name: str, description: str, content: str) -> tuple[int, bool]:
    name = name.strip()[:80]
    # chacun ne met à jour que ses propres compétences ; une compétence partagée ne change que par un enregistrement partagé
    existing = db.one("SELECT id FROM skills WHERE lower(name) = lower(?) AND user_id IS ?", (name, user_id))
    if existing:
        sid = existing["id"]
        db.update("skills", "id = ?", (sid,), description=description, content=content, updated_at=now())
        db.run("DELETE FROM skills_fts WHERE rowid = ?", (sid,))
        created = False
    else:
        sid = db.insert("skills", user_id=user_id, name=name, description=description, content=content,
                        created_at=now(), updated_at=now())
        created = True
    db.run("INSERT OR REPLACE INTO skills_fts(rowid, name, description, content) VALUES(?,?,?,?)", (sid, name, description, content))
    return sid, created


def list_skills(user_id: int) -> list[dict]:
    return db.all("SELECT id, user_id, name, description, content, uses, updated_at FROM skills "
                  "WHERE user_id = ? OR user_id IS NULL ORDER BY uses DESC, updated_at DESC", (user_id,))


def delete_skill(user_id: int, sid: int, admin: bool = False) -> bool:
    if admin:
        return bool(db.run("DELETE FROM skills WHERE id = ?", (sid,)))
    return bool(db.run("DELETE FROM skills WHERE id = ? AND user_id = ?", (sid, user_id)))


def relevant_skills(user_id: int, query: str, k: int = 2) -> list[dict]:
    q = fts_query(query)
    if not q:
        return []
    rows = db.all("SELECT s.id, s.name, s.description, s.content FROM skills_fts f JOIN skills s ON s.id = f.rowid "
                  "WHERE skills_fts MATCH ? AND (s.user_id = ? OR s.user_id IS NULL) ORDER BY bm25(skills_fts, 5.0, 3.0, 1.0) "
                  "LIMIT 10", (q, user_id))
    words = [w.strip('"*') for w in q.split(" OR ")]

    def score(r: dict) -> float:
        head = (r["name"] + " " + r["description"]).lower()
        body = r["content"].lower()
        return sum(2.0 if w[:5] in head else (0.5 if w[:5] in body else 0) for w in words)

    rows = sorted((r for r in rows if score(r) >= 2.0), key=score, reverse=True)[:k]  # correspondances franches
    if rows:
        db.run(f"UPDATE skills SET uses = uses + 1 WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
    return rows


def _ensure_loop_task(coro) -> None:
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        pass


def purge_user_index(user_id: int) -> None:
    """Retire les entrées plein texte d'un utilisateur (à appeler avant de le supprimer)."""
    db.run("DELETE FROM messages_fts WHERE user_id = ?", (user_id,))
    db.run("DELETE FROM memories_fts WHERE rowid IN (SELECT id FROM memories WHERE user_id = ?)", (user_id,))
    db.run("DELETE FROM skills_fts WHERE rowid IN (SELECT id FROM skills WHERE user_id = ?)", (user_id,))
    db.run("DELETE FROM skills WHERE user_id = ?", (user_id,))
