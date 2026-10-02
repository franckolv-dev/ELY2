"""Agenda et contacts : Google si connecté, sinon agenda et carnet intégrés.

L'agenda intégré est publié en flux iCal (abonnement depuis le téléphone) et
envoie des rappels en notification.
"""
from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from ..config import settings
from ..db import db, now
from ..integrations import connected
from ..integrations import google as g
from . import ToolContext, ToolResult, tool


def known_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:  # nom inconnu ou mal formé
        return False


def user_tz(user: dict) -> str:
    """Fuseau de la personne ; un fuseau inconnu ne fait jamais échouer ses tâches ni les routines : celui d'Ely."""
    tz = (user.get("settings") or {}).get("timezone") or settings.timezone
    return tz if known_tz(tz) else settings.timezone if known_tz(settings.timezone) else "Europe/Paris"


def parse_local(value: str, tz: str) -> dt.datetime:
    """Date ISO → heure locale de l'utilisateur (une heure UTC « …Z » est convertie)."""
    v = value.strip().replace(" ", "T")
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(v) if "T" in v else dt.datetime.fromisoformat(v + "T00:00")
    return d.astimezone(ZoneInfo(tz)) if d.tzinfo else d.replace(tzinfo=ZoneInfo(tz))


def _google(ctx: ToolContext) -> bool:
    return connected(ctx.user_id, "google")


def _fmt_event(e: dict) -> str:
    extra = f" · {e['location']}" if e.get("location") else ""
    who = f" · avec {', '.join(e['attendees'])}" if e.get("attendees") else ""
    return f"[{e['id']}] {e['start'][:16].replace('T', ' ')} → {e['end'][11:16] or e['end'][:10]} : {e['title']}{extra}{who}"


@tool("calendar_list", "Liste les événements de l'agenda sur une période (par défaut : les 7 prochains jours).",
      {"start": {"type": "string", "description": "Début ISO (AAAA-MM-JJ ou AAAA-MM-JJTHH:MM)"},
       "end": {"type": "string", "description": "Fin ISO"}, "query": {"type": "string", "description": "Filtre texte"}},
      [], label="Agenda", icon="📅", timeout=60, effects=False)
async def calendar_list(ctx: ToolContext, start: str = "", end: str = "", query: str = "") -> ToolResult:
    tz = user_tz(ctx.user)
    s = parse_local(start, tz) if start else dt.datetime.now(ZoneInfo(tz))
    e = parse_local(end, tz) if end else s + dt.timedelta(days=7)
    if e <= s:
        e = s + dt.timedelta(days=1)
    if _google(ctx):
        items = await g.cal_list(ctx.user_id, s.isoformat(), e.isoformat(), query or None)
    else:
        rows = db.all("SELECT * FROM events WHERE user_id = ? AND end >= ? AND start <= ? ORDER BY start",
                      (ctx.user_id, s.strftime("%Y-%m-%dT%H:%M"), e.strftime("%Y-%m-%dT%H:%M")))
        items = [{"id": f"local-{r['id']}", "title": r["title"], "start": r["start"], "end": r["end"], "location": r["location"],
                  "description": r["description"]} for r in rows
                 if not query or query.lower() in (r["title"] + r["description"] + r["location"]).lower()]
    if not items:
        return ToolResult(f"Aucun événement entre le {s:%d/%m %H:%M} et le {e:%d/%m %H:%M}.")
    return ToolResult(f"{len(items)} événement(s) :\n" + "\n".join(_fmt_event(i) for i in items))


@tool("calendar_add", "Ajoute un rendez-vous / événement à l'agenda de l'utilisateur. Heures locales ISO (AAAA-MM-JJTHH:MM).",
      {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string", "description": "défaut : début + 1 h"},
       "all_day": {"type": "boolean"}, "location": {"type": "string"}, "description": {"type": "string"},
       "attendees": {"type": "array", "items": {"type": "string"}, "description": "E-mails des invités"},
       "reminder_minutes": {"type": "integer", "description": "Rappel N minutes avant (défaut 30)"}},
      ["title", "start"], label="Ajout agenda", icon="🗓️", timeout=60)
async def calendar_add(ctx: ToolContext, title: str, start: str, end: str = "", all_day: bool = False, location: str = "",
                       description: str = "", attendees: list[str] | None = None, reminder_minutes: int = 30) -> ToolResult:
    tz = user_tz(ctx.user)
    s = parse_local(start, tz)
    e = parse_local(end, tz) if end else s + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
    ev = {"title": title, "start": s.strftime("%Y-%m-%dT%H:%M"), "end": e.strftime("%Y-%m-%dT%H:%M"), "all_day": all_day,
          "location": location, "description": description, "attendees": attendees or [], "reminder_minutes": reminder_minutes}
    if _google(ctx):
        res = await g.cal_add(ctx.user_id, ev, tz)
        return ToolResult(f"Événement ajouté à Google Agenda : {title} le {s:%d/%m/%Y à %H:%M} (id {res.get('id')}). {res.get('htmlLink', '')}")
    eid = db.insert("events", user_id=ctx.user_id, uid=uuid.uuid4().hex, title=title, start=ev["start"], end=ev["end"],
                    all_day=int(all_day), location=location, description=description, reminder_minutes=reminder_minutes,
                    created_at=now(), updated_at=now())
    return ToolResult(f"Événement ajouté à l'agenda Ely : {title} le {s:%d/%m/%Y à %H:%M} (id local-{eid}). "
                      "Rappel envoyé en notification.")


@tool("calendar_update", "Modifie ou supprime (delete=true) un événement de l'agenda.",
      {"id": {"type": "string"}, "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
       "location": {"type": "string"}, "description": {"type": "string"}, "delete": {"type": "boolean"}},
      ["id"], label="Modification agenda", icon="🗓️", timeout=60)
async def calendar_update(ctx: ToolContext, id: str, delete: bool = False, **fields) -> ToolResult:
    tz = user_tz(ctx.user)
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    if id.startswith("local-"):
        lid = int(id[6:])
        if not db.one("SELECT id FROM events WHERE id = ? AND user_id = ?", (lid, ctx.user_id)):
            return ToolResult(f"Événement {id} introuvable", is_error=True)
        if delete:
            db.run("DELETE FROM events WHERE id = ?", (lid,))
            return ToolResult(f"Événement {id} supprimé.")
        for k in ("start", "end"):
            if k in fields:
                fields[k] = parse_local(fields[k], tz).strftime("%Y-%m-%dT%H:%M")
        if fields:
            db.update("events", "id = ?", (lid,), **fields, updated_at=now(), reminded=0)
        return ToolResult(f"Événement {id} mis à jour : {', '.join(fields)}")
    if not _google(ctx):
        return ToolResult("Événement inconnu.", is_error=True)
    if delete:
        await g.cal_delete(ctx.user_id, id)
        return ToolResult(f"Événement {id} supprimé de Google Agenda.")
    for k in ("start", "end"):
        if k in fields:
            fields[k] = parse_local(fields[k], tz).strftime("%Y-%m-%dT%H:%M")
    await g.cal_update(ctx.user_id, id, fields, tz)
    return ToolResult(f"Événement mis à jour : {', '.join(fields)}")


def _fmt_contact(c: dict) -> str:
    bits = [c.get(k) for k in ("email", "phone", "company", "address") if c.get(k)]
    if c.get("birthday"):
        bits.append(f"né(e) le {c['birthday']}")
    note = f"\n   Notes : {c['notes'][:200]}" if c.get("notes") else ""
    return f"[{c['id']}] {c['name']} — {' · '.join(bits)}{note}"


@tool("contacts_search", "Cherche dans les contacts de l'utilisateur (nom, e-mail, téléphone, entreprise).",
      {"query": {"type": "string"}}, ["query"], label="Contacts", icon="👤", timeout=60, effects=False)
async def contacts_search(ctx: ToolContext, query: str) -> ToolResult:
    if _google(ctx):
        items = await g.contacts_search(ctx.user_id, query)
    else:
        q = f"%{query.lower()}%"
        rows = db.all("SELECT * FROM contacts WHERE user_id = ? AND (lower(name) LIKE ? OR lower(email) LIKE ? OR phone LIKE ? "
                      "OR lower(company) LIKE ? OR lower(notes) LIKE ?) ORDER BY name LIMIT 30", (ctx.user_id, q, q, q, q, q))
        items = [{**r, "id": f"local-{r['id']}"} for r in rows]
    if not items:
        return ToolResult(f"Aucun contact ne correspond à « {query} ».")
    return ToolResult("\n".join(_fmt_contact(c) for c in items))


@tool("contacts_save", "Ajoute un contact, ou met à jour un contact existant si id est fourni.",
      {"id": {"type": "string", "description": "Pour une mise à jour"}, "name": {"type": "string"}, "email": {"type": "string"},
       "phone": {"type": "string"}, "company": {"type": "string"}, "address": {"type": "string"},
       "birthday": {"type": "string", "description": "AAAA-MM-JJ"}, "notes": {"type": "string"}},
      [], label="Enregistrement contact", icon="📇", timeout=60)
async def contacts_save(ctx: ToolContext, id: str = "", **fields) -> ToolResult:
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    if not id and not fields.get("name"):
        return ToolResult("Le nom est obligatoire pour un nouveau contact.", is_error=True)
    if id.startswith("local-") or (not id and not _google(ctx)):
        if id:
            lid = int(id[6:])
            db.update("contacts", "id = ? AND user_id = ?", (lid, ctx.user_id), **fields, updated_at=now())
            return ToolResult(f"Contact {id} mis à jour.")
        cid = db.insert("contacts", user_id=ctx.user_id, **fields, created_at=now(), updated_at=now())
        return ToolResult(f"Contact ajouté : {fields['name']} (id local-{cid}).")
    if id:
        c = await g.contacts_update(ctx.user_id, id, fields)
        return ToolResult(f"Contact Google mis à jour : {_fmt_contact(c)}")
    c = await g.contacts_create(ctx.user_id, fields)
    return ToolResult(f"Contact ajouté à Google Contacts : {_fmt_contact(c)}")


def ics_feed(user_id: int) -> str:
    """Flux iCal de l'agenda intégré (abonnement depuis Google Agenda, Apple Calendrier, Android…)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    from ..auth import get_user

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ely//Agenda//FR", "X-WR-CALNAME:Ely", "CALSCALE:GREGORIAN"]
    tz = user_tz(get_user(user_id) or {})
    for r in db.all("SELECT * FROM events WHERE user_id = ? ORDER BY start", (user_id,)):
        s = parse_local(r["start"], tz).astimezone(dt.timezone.utc)
        e = parse_local(r["end"], tz).astimezone(dt.timezone.utc)
        lines += ["BEGIN:VEVENT", f"UID:{r['uid']}@ely", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"]
        if r["all_day"]:
            lines += [f"DTSTART;VALUE=DATE:{r['start'][:10].replace('-', '')}", f"DTEND;VALUE=DATE:{r['end'][:10].replace('-', '')}"]
        else:
            lines += [f"DTSTART:{s:%Y%m%dT%H%M%SZ}", f"DTEND:{e:%Y%m%dT%H%M%SZ}"]
        lines.append(f"SUMMARY:{esc(r['title'])}")
        if r["location"]:
            lines.append(f"LOCATION:{esc(r['location'])}")
        if r["description"]:
            lines.append(f"DESCRIPTION:{esc(r['description'])}")
        if r["reminder_minutes"]:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(r['title'])}",
                      f"TRIGGER:-PT{int(r['reminder_minutes'])}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
