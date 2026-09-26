"""Apprentissage après chaque tâche : Ely connaît de mieux en mieux chaque utilisateur.

Un seul appel au modèle local (gratuit avec LM Studio) met à jour le profil, extrait
les faits durables et, après une tâche complexe réussie, écrit une compétence.
"""
from __future__ import annotations

import json
import logging
import re

from ..auth import update_user_settings
from ..db import db
from ..llm import parse_json_loose, registry
from ..llm.base import message_text
from . import store

log = logging.getLogger("ely.learner")

PROFILE_SECTIONS = "Identité · Proches · Travail · Lieux · Préférences et habitudes · Santé · Comptes et services · Style de communication"


def _run_digest(conversation_id: int, run_id: int) -> tuple[str, int, str]:
    rows = db.all("SELECT data FROM messages WHERE conversation_id = ? AND run_id = ? ORDER BY id", (conversation_id, run_id))
    lines, n_tools = [], 0
    for r in rows:
        m = json.loads(r["data"])
        if m.get("kind") in ("control", "note"):
            continue
        if m["role"] == "user":
            lines.append(f"UTILISATEUR : {message_text(m)[:3000]}")
        elif m["role"] == "assistant":
            if m.get("tool_calls"):
                n_tools += len(m["tool_calls"])
                for tc in m["tool_calls"]:
                    lines.append(f"  → outil {tc['name']}({json.dumps(tc.get('arguments', {}), ensure_ascii=False)[:250]})")
            if m.get("content"):
                lines.append(f"ELY : {m['content'][:2500]}")
        elif m["role"] == "tool":
            lines.append(f"  ← {'ÉCHEC ' if m.get('is_error') else ''}{(m.get('content') or '')[:250]}")
    status = db.val("SELECT status FROM runs WHERE id = ?", (run_id,)) or ""
    return "\n".join(lines)[-24000:], n_tools, status


# Demandes explicites de tutoiement ou de vouvoiement (retenues même pour un échange très court)
_VOUS = re.compile(r"vouvoie[- ]?moi|me vouvoyer|ne me tutoie(?:s)? pas|arr[eê]te de me tutoyer|ne (?:plus |pas )me tutoyer", re.I)
_TU = re.compile(r"tutoie[- ]?moi|me tutoyer|se tutoyer|tutoyons[- ]nous", re.I)


def address_request(text: str) -> str | None:
    if _VOUS.search(text):
        return "vous"
    return "tu" if _TU.search(text) else None


async def learn_from_run(user: dict, conversation_id: int, run_id: int) -> dict | None:
    asked = [address_request(message_text(json.loads(r["data"]))) for r in db.all(
        "SELECT data FROM messages WHERE conversation_id = ? AND run_id = ? AND role = 'user' ORDER BY id", (conversation_id, run_id))]
    asked = [a for a in asked if a]
    if asked:
        update_user_settings(user["id"], address=asked[-1])
    digest, n_tools, status = _run_digest(conversation_id, run_id)
    if not digest or (n_tools == 0 and len(digest) < 120):
        return None
    profile = store.get_profile(user["id"])
    want_skill = n_tools >= 4 and status == "done"
    prompt = f"""Tu mets à jour la mémoire d'Ely, l'assistante personnelle de {user['name']}.

PROFIL ACTUEL :
{profile or '(vide)'}

ÉCHANGE QUI VIENT D'AVOIR LIEU :
{digest}

Tâches :
1. "profile" : si l'échange révèle des informations DURABLES sur {user['name']} (identité, proches, travail, lieux, préférences,
   habitudes, santé, comptes utilisés, façon de communiquer), renvoie le profil COMPLET mis à jour en markdown, sections :
   {PROFILE_SECTIONS} (omets les sections vides). Fusionne, corrige, reste concis (max 350 mots). Sinon null.
2. "facts" : faits précis et durables utiles plus tard (ex. « Le médecin traitant de {user['name']} est le Dr X, à Lyon,
   réservable sur Doctolib »). Phrases autonomes. Pas d'infos éphémères ni de ce qui est déjà dans le profil. Liste vide sinon.
3. "address" : "tu" si {user['name']} a demandé à être tutoyé(e) dans cet échange, "vous" s'il ou elle a demandé
   à être vouvoyé(e), sinon null.
4. "skill" : {"si la tâche a demandé plusieurs étapes et a réussi, une procédure réutilisable (nom court, description d'une phrase " +
   "« quand l'utiliser », contenu markdown : étapes concrètes, URLs, pièges et solutions ; comment RÉUSSIR, jamais d'interdiction, " +
   "sans données personnelles). Sinon null." if want_skill else "null"}

Réponds uniquement en JSON : {{"profile": ..., "facts": [...], "address": ..., "skill": ...}}"""
    try:
        raw = await registry.complete(prompt, role="local", max_tokens=2500, user_id=user["id"], purpose="memory")
        data = parse_json_loose(raw)
    except Exception as e:
        log.info("apprentissage ignoré : %s", e)
        return None
    if isinstance(data.get("profile"), str) and len(data["profile"].strip()) > 20:
        store.set_profile(user["id"], data["profile"][:5000])
    for fact in (data.get("facts") or [])[:8]:
        if isinstance(fact, str) and len(fact) > 10:
            await store.add_memory(user["id"], fact, "fait", source="auto")
    if data.get("address") in ("tu", "vous"):
        update_user_settings(user["id"], address=data["address"])
    sk = data.get("skill")
    if want_skill and isinstance(sk, dict) and sk.get("name") and sk.get("content"):
        store.save_skill(user["id"], sk["name"], sk.get("description", ""), sk["content"])
    try:
        await store.backfill_embeddings(50)
    except Exception:
        pass
    return data


async def make_title(conversation_id: int, user_id: int) -> str:
    rows = db.all("SELECT data FROM messages WHERE conversation_id = ? AND role IN ('user', 'assistant') ORDER BY id LIMIT 4",
                  (conversation_id,))
    text = "\n".join(message_text(json.loads(r["data"]))[:600] for r in rows)
    if not text.strip():
        return ""
    raw = await registry.complete(f"Donne un titre de 2 à 6 mots, en français, pour cette conversation. Réponds uniquement "
                                  f"par le titre, sans guillemets.\n\n{text}", role="local", max_tokens=300, user_id=user_id, purpose="title")
    title = raw.strip().strip('"«»').split("\n")[0][:60]
    return title
