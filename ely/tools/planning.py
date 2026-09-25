"""Planification, questions à l'utilisateur, notifications, identifiants."""
from __future__ import annotations

import datetime as dt
import time
from zoneinfo import ZoneInfo

from croniter import croniter

from ..db import db, now
from ..notify import notify as send_notification
from . import ToolContext, ToolResult, tool
from .pim import parse_local, user_tz


def next_cron(expr: str, tz: str, after: float | None = None) -> float:
    base = dt.datetime.fromtimestamp(after or time.time(), ZoneInfo(tz))
    return croniter(expr, base).get_next(dt.datetime).timestamp()


@tool("schedule", """Planifie une tâche que tu exécuteras seul plus tard : rappel, veille, rapport récurrent, publication programmée…
action=create : instruction (ce que tu devras faire, formulé de façon autonome) + when (date/heure locale ISO, une fois)
ou cron (récurrent, 5 champs, heure locale, ex. « 0 8 * * 1-5 » = jours ouvrés à 8 h). action=list · action=cancel(id).""",
      {"action": {"type": "string", "enum": ["create", "list", "cancel"]}, "instruction": {"type": "string"},
       "when": {"type": "string"}, "cron": {"type": "string"}, "id": {"type": "integer"}},
      ["action"], label="Planification", icon="⏰", timeout=20)
async def schedule(ctx: ToolContext, action: str, instruction: str = "", when: str = "", cron: str = "", id: int | None = None) -> ToolResult:
    tz = user_tz(ctx.user)
    if action == "list":
        rows = db.all("SELECT * FROM schedules WHERE user_id = ? AND enabled = 1 ORDER BY next_run", (ctx.user_id,))
        if not rows:
            return ToolResult("Aucune tâche planifiée.")
        return ToolResult("\n".join(
            f"#{r['id']} {'(' + r['cron'] + ') ' if r['cron'] else ''}prochaine : "
            f"{dt.datetime.fromtimestamp(r['next_run'], ZoneInfo(tz)):%d/%m/%Y %H:%M} — {r['instruction'][:150]}" for r in rows))
    if action == "cancel":
        n = db.run("UPDATE schedules SET enabled = 0 WHERE id = ? AND user_id = ?", (id, ctx.user_id))
        return ToolResult(f"Tâche #{id} annulée." if n else f"Tâche #{id} introuvable.", is_error=not n)
    if not instruction or not (when or cron):
        return ToolResult("Il faut instruction et when (ou cron).", is_error=True)
    if cron:
        if not croniter.is_valid(cron):
            return ToolResult(f"Expression cron invalide : {cron}", is_error=True)
        nxt = next_cron(cron, tz)
    else:
        nxt = parse_local(when, tz).timestamp()
        if nxt < time.time() - 60:
            return ToolResult("Cette date est déjà passée.", is_error=True)
    sid = db.insert("schedules", user_id=ctx.user_id, conversation_id=ctx.conversation_id, instruction=instruction,
                    cron=cron, next_run=nxt, enabled=1, created_at=now())
    when_txt = dt.datetime.fromtimestamp(nxt, ZoneInfo(tz)).strftime("%d/%m/%Y à %H:%M")
    return ToolResult(f"Tâche #{sid} planifiée{' (récurrente : ' + cron + ')' if cron else ''}, prochaine exécution le {when_txt}.")


@tool("ask_user", """Pose une question à l'utilisateur et attend sa réponse (il est notifié sur son téléphone).
UNIQUEMENT si c'est indispensable : code reçu par SMS, mot de passe inconnu, choix vraiment personnel sans défaut raisonnable.
Sinon décide toi-même en t'appuyant sur ses préférences connues. Propose des options quand c'est possible.""",
      {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}},
      ["question"], label="Question", icon="❓", timeout=7 * 86400, subagent=False)
async def ask_user(ctx: ToolContext, question: str, options: list[str] | None = None) -> ToolResult:
    if not ctx.ask:
        return ToolResult("Personne ne peut répondre maintenant : choisis l'option la plus raisonnable et continue.", is_error=True)
    answer = await ctx.ask(question, options)
    return ToolResult(f"Réponse de l'utilisateur : {answer}")


@tool("notify", "Envoie une notification sur le téléphone/ordinateur de l'utilisateur (utile pour les tâches planifiées ou longues).",
      {"title": {"type": "string"}, "message": {"type": "string"}}, ["message"], label="Notification", icon="🔔", timeout=30)
async def notify(ctx: ToolContext, message: str, title: str = "Ely") -> ToolResult:
    n = await send_notification(ctx.user_id, title, message, url=f"/?c={ctx.conversation_id}")
    return ToolResult(f"Notification envoyée ({n} appareil(s))." if n else
                      "Aucun appareil abonné aux notifications : le message reste visible dans la conversation.")


@tool("credentials", """Coffre des identifiants de l'utilisateur pour se connecter aux sites (Doctolib, LinkedIn, impots.gouv…).
action=get(service) · list · save(service, username, password, url?, notes?) — enregistre-les dès que l'utilisateur les donne.""",
      {"action": {"type": "string", "enum": ["get", "list", "save"]}, "service": {"type": "string"},
       "username": {"type": "string"}, "password": {"type": "string"}, "url": {"type": "string"}, "notes": {"type": "string"}},
      ["action"], label="Identifiants", icon="🔑", timeout=20)
async def credentials(ctx: ToolContext, action: str, service: str = "", username: str = "", password: str = "",
                      url: str = "", notes: str = "") -> ToolResult:
    if action == "list":
        rows = db.all("SELECT service, url, username FROM credentials WHERE user_id = ? ORDER BY service", (ctx.user_id,))
        return ToolResult("\n".join(f"- {r['service']} ({r['url'] or '—'}) : {r['username']}" for r in rows) or "Coffre vide.")
    if not service:
        return ToolResult("service manquant", is_error=True)
    if action == "save":
        row = db.one("SELECT id FROM credentials WHERE user_id = ? AND lower(service) = lower(?)", (ctx.user_id, service))
        fields = {k: v for k, v in {"username": username, "password": password, "url": url, "notes": notes}.items() if v}
        if row:
            db.update("credentials", "id = ?", (row["id"],), **fields, updated_at=now())
        else:
            db.insert("credentials", user_id=ctx.user_id, service=service, **fields, created_at=now(), updated_at=now())
        return ToolResult(f"Identifiants « {service} » enregistrés.")
    q = f"%{service.lower()}%"
    rows = db.all("SELECT * FROM credentials WHERE user_id = ? AND (lower(service) LIKE ? OR lower(url) LIKE ?)", (ctx.user_id, q, q))
    if not rows:
        return ToolResult(f"Aucun identifiant pour « {service} ». Demande-les avec ask_user puis enregistre-les (action=save).", is_error=True)
    return ToolResult("\n".join(f"{r['service']} — url : {r['url'] or '?'} — identifiant : {r['username']} — mot de passe : {r['password']}"
                                + (f" — notes : {r['notes']}" if r["notes"] else "") for r in rows))
