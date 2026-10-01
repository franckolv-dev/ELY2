"""Planificateur : tâches programmées, rappels d'agenda, auto-amélioration quotidienne."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from .auth import get_user
from .config import settings
from .db import db, now

log = logging.getLogger("ely.scheduler")


async def run_due_schedules() -> None:
    from .agent.runner import runner
    from .tools.planning import next_cron
    from .tools.pim import user_tz

    for s in db.all("SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ?", (time.time(),)):
        user = get_user(s["user_id"])
        if not user:
            continue
        if s["cron"]:
            db.update("schedules", "id = ?", (s["id"],), next_run=next_cron(s["cron"], user_tz(user)), last_run=now())
        else:
            db.update("schedules", "id = ?", (s["id"],), enabled=0, last_run=now())
        cid = s["conversation_id"]
        if not cid or not db.one("SELECT id FROM conversations WHERE id = ?", (cid,)):
            cid = db.insert("conversations", user_id=user["id"], title="⏰ Tâches planifiées", channel="schedule",
                            created_at=now(), updated_at=now())
            db.update("schedules", "id = ?", (s["id"],), conversation_id=cid)
        busy = runner.states.get(cid)
        if busy and busy.task and not busy.task.done():  # une autre tâche y tourne : celle-ci démarre à part, sans s'y mêler
            cid = db.insert("conversations", user_id=user["id"], title=f"⏰ Tâche planifiée #{s['id']}", channel="schedule",
                            created_at=now(), updated_at=now())
        try:
            await runner.submit(user, cid, f"[Tâche planifiée #{s['id']}] {s['instruction']}", channel="schedule", kind="scheduled")
            db.update("schedules", "id = ?", (s["id"],), last_status="lancée")
        except Exception as e:
            db.update("schedules", "id = ?", (s["id"],), last_status=f"erreur : {e}"[:200])


async def calendar_reminders() -> None:
    from .notify import notify
    from .tools.pim import parse_local, user_tz

    nowdt = dt.datetime.now(dt.timezone.utc)
    horizon = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    for e in db.all("SELECT * FROM events WHERE reminded = 0 AND reminder_minutes > 0 AND start <= ?", (horizon,)):
        user = get_user(e["user_id"])
        if not user:
            continue
        start = parse_local(e["start"], user_tz(user))
        if start - dt.timedelta(minutes=e["reminder_minutes"]) <= nowdt < start:
            db.update("events", "id = ?", (e["id"],), reminded=1)
            where = f" · {e['location']}" if e["location"] else ""
            await notify(user["id"], f"⏰ {e['title']}", f"À {start.astimezone(ZoneInfo(user_tz(user))):%H:%M}{where}", tag=f"event-{e['id']}")
        elif start < nowdt:
            db.update("events", "id = ?", (e["id"],), reminded=1)


async def daily_self_improvement() -> None:
    if not db.get_setting("selfdev_auto", True):
        return
    local = dt.datetime.now(ZoneInfo(settings.timezone))
    hour = int(db.get_setting("selfdev_hour", 4))
    today = local.strftime("%Y-%m-%d")
    if local.hour != hour or db.get_setting("selfdev_last_day", "") == today:
        return
    db.set_setting("selfdev_last_day", today)
    recent = db.val("SELECT COUNT(*) FROM runs WHERE created_at > ? AND status != 'running'", (time.time() - 86400,))
    if not recent:
        return
    admin = db.one("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    if admin:
        from .selfdev.tools import start_session

        start_session(get_user(admin["id"]), "Session quotidienne : analyse les tâches des dernières 24 h (ely_metrics days=1) "
                                              "et applique les améliorations les plus utiles.")


async def scheduler_loop() -> None:
    tick = 0
    while True:
        for job in (run_due_schedules, calendar_reminders, daily_self_improvement):
            try:
                await job()
            except Exception:
                log.exception("planificateur : %s", job.__name__)
        tick += 1
        if tick % 90 == 0:
            try:
                from .memory.store import backfill_embeddings

                await backfill_embeddings()
            except Exception:
                pass
        await asyncio.sleep(20)
