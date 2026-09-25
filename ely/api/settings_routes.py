"""Routes : mémoire, connexions, identifiants, tâches planifiées, notifications."""
from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from .. import auth
from ..config import settings
from ..db import db, now
from ..integrations import get, google, mail, put, remove, social, verify_state
from ..memory import store
from ..notify import notify, save_subscription, vapid_keys

router = APIRouter()


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------- mémoire
@router.get("/api/memory")
def memory(user=Depends(auth.current_user)):
    return {"profile": store.get_profile(user["id"]), "memories": store.list_memories(user["id"]),
            "skills": store.list_skills(user["id"])}


class Text(BaseModel):
    content: str
    category: str = "fait"


@router.put("/api/memory/profile")
def put_profile(body: Text, user=Depends(auth.current_user)):
    store.set_profile(user["id"], body.content)
    return {"ok": True}


@router.post("/api/memory")
async def add_memory(body: Text, user=Depends(auth.current_user)):
    mid, new = await store.add_memory(user["id"], body.content, body.category, source="utilisateur")
    return {"id": mid, "new": new}


@router.delete("/api/memory/{mid}")
def del_memory(mid: int, user=Depends(auth.current_user)):
    return {"ok": store.delete_memory(user["id"], mid)}


class SkillIn(BaseModel):
    name: str
    description: str
    content: str


@router.put("/api/skills/{sid}")
def put_skill(sid: int, body: SkillIn, user=Depends(auth.current_user)):
    row = db.one("SELECT user_id FROM skills WHERE id = ?", (sid,))
    if not row or (row["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "Compétence introuvable")
    db.update("skills", "id = ?", (sid,), name=body.name, description=body.description, content=body.content, updated_at=now())
    db.run("DELETE FROM skills_fts WHERE rowid = ?", (sid,))
    db.run("INSERT INTO skills_fts(rowid, name, description, content) VALUES(?,?,?,?)", (sid, body.name, body.description, body.content))
    return {"ok": True}


@router.delete("/api/skills/{sid}")
def del_skill(sid: int, user=Depends(auth.current_user)):
    ok = store.delete_skill(user["id"], sid, admin=user["role"] == "admin")
    db.run("DELETE FROM skills_fts WHERE rowid = ?", (sid,))
    return {"ok": ok}


# ---------------------------------------------------------------------- connexions
@router.get("/api/integrations")
def integrations(request: Request, user=Depends(auth.current_user)):
    s = user["settings"]
    ics = s.get("ics_token")
    if not ics:
        ics = secrets.token_urlsafe(16)
        auth.update_user_settings(user["id"], ics_token=ics)
    g = get(user["id"], "google")
    e = get(user["id"], "email")
    tg = get(user["id"], "telegram")
    base = settings.external_url(_base(request))
    return {
        "google": {"available": google.enabled(), "connected": bool(g), "email": g.get("email", ""),
                   "redirect_uri": google.redirect_uri(_base(request))},
        "email": {"connected": bool(e), "address": e.get("address", ""), "imap_host": e.get("imap_host", ""),
                  "smtp_host": e.get("smtp_host", "")},
        "linkedin": {"available": social.linkedin_enabled(), "connected": bool(get(user["id"], "linkedin")),
                     "name": get(user["id"], "linkedin").get("name", "")},
        "facebook": {"connected": bool(get(user["id"], "facebook").get("page_token")), "page_id": get(user["id"], "facebook").get("page_id", "")},
        "telegram": {"available": bool(settings.telegram_bot_token), "linked": bool(tg.get("chat_id")),
                     "username": tg.get("username", "")},
        "ics_url": f"{base}/ics/{ics}.ics",
    }


@router.get("/api/integrations/google/start")
def google_start(request: Request, user=Depends(auth.current_user)):
    if not google.enabled():
        raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET non configurés dans .env")
    return RedirectResponse(google.auth_url(user["id"], _base(request)))


@router.get("/api/integrations/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    uid = verify_state(state, "google")
    if error or not uid:
        return RedirectResponse(f"/?settings=connexions&error={error or 'state'}")
    await google.exchange_code(uid, code, _base(request))
    return RedirectResponse("/?settings=connexions&ok=google")


@router.get("/api/integrations/linkedin/start")
def linkedin_start(request: Request, user=Depends(auth.current_user)):
    if not social.linkedin_enabled():
        raise HTTPException(400, "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET non configurés")
    return RedirectResponse(social.linkedin_auth_url(user["id"], _base(request)))


@router.get("/api/integrations/linkedin/callback")
async def linkedin_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    uid = verify_state(state, "linkedin")
    if error or not uid:
        return RedirectResponse(f"/?settings=connexions&error={error or 'state'}")
    await social.linkedin_exchange(uid, code, _base(request))
    return RedirectResponse("/?settings=connexions&ok=linkedin")


class EmailIn(BaseModel):
    address: str
    password: str
    username: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 0
    from_name: str = ""


@router.post("/api/integrations/email")
async def email_connect(body: EmailIn, user=Depends(auth.current_user)):
    cfg = {**mail.preset_for(body.address), **{k: v for k, v in body.model_dump().items() if v}}
    try:
        await asyncio.to_thread(mail.test_login, cfg)
    except Exception as e:
        raise HTTPException(400, f"Connexion IMAP impossible ({cfg['imap_host']}) : {e}. "
                                 "Astuce : utilise un « mot de passe d'application ».")
    put(user["id"], "email", cfg)
    return {"ok": True, "imap_host": cfg["imap_host"], "smtp_host": cfg["smtp_host"]}


class FacebookIn(BaseModel):
    page_id: str
    page_token: str


@router.post("/api/integrations/facebook")
def facebook_connect(body: FacebookIn, user=Depends(auth.current_user)):
    put(user["id"], "facebook", body.model_dump())
    return {"ok": True}


@router.post("/api/integrations/telegram/link")
def telegram_link(user=Depends(auth.current_user)):
    if not settings.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN non configuré")
    code = secrets.token_hex(4)
    put(user["id"], "telegram_pending", {"code": code, "at": now()})
    from ..channels.telegram import bot_username

    return {"code": code, "url": f"https://t.me/{bot_username()}?start={code}" if bot_username() else "", "command": f"/start {code}"}


@router.delete("/api/integrations/{provider}")
def disconnect(provider: str, user=Depends(auth.current_user)):
    remove(user["id"], provider)
    return {"ok": True}


@router.get("/ics/{token}.ics")
def ics(token: str):
    from ..tools.pim import ics_feed

    row = db.one("SELECT id FROM users WHERE json_extract(settings, '$.ics_token') = ?", (token,))
    if not row:
        raise HTTPException(404, "Agenda introuvable")
    return PlainTextResponse(ics_feed(row["id"]), media_type="text/calendar; charset=utf-8")


# ---------------------------------------------------------------------- identifiants
class CredIn(BaseModel):
    service: str
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""


@router.get("/api/credentials")
def list_creds(user=Depends(auth.current_user)):
    return db.all("SELECT id, service, url, username, notes, updated_at FROM credentials WHERE user_id = ? ORDER BY service", (user["id"],))


@router.post("/api/credentials")
def add_cred(body: CredIn, user=Depends(auth.current_user)):
    row = db.one("SELECT id FROM credentials WHERE user_id = ? AND lower(service) = lower(?)", (user["id"], body.service))
    if row:
        db.update("credentials", "id = ?", (row["id"],), **body.model_dump(), updated_at=now())
        return {"id": row["id"]}
    return {"id": db.insert("credentials", user_id=user["id"], **body.model_dump(), created_at=now(), updated_at=now())}


@router.delete("/api/credentials/{cid}")
def del_cred(cid: int, user=Depends(auth.current_user)):
    db.run("DELETE FROM credentials WHERE id = ? AND user_id = ?", (cid, user["id"]))
    return {"ok": True}


# ---------------------------------------------------------------------- tâches planifiées
@router.get("/api/schedules")
def schedules(user=Depends(auth.current_user)):
    return db.all("SELECT * FROM schedules WHERE user_id = ? ORDER BY enabled DESC, next_run", (user["id"],))


class ScheduleUpdate(BaseModel):
    enabled: bool


@router.patch("/api/schedules/{sid}")
def toggle_schedule(sid: int, body: ScheduleUpdate, user=Depends(auth.current_user)):
    db.run("UPDATE schedules SET enabled = ? WHERE id = ? AND user_id = ?", (int(body.enabled), sid, user["id"]))
    return {"ok": True}


@router.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, user=Depends(auth.current_user)):
    db.run("DELETE FROM schedules WHERE id = ? AND user_id = ?", (sid, user["id"]))
    return {"ok": True}


# ---------------------------------------------------------------------- notifications
@router.get("/api/push/key")
def push_key():
    return {"key": vapid_keys()["public"]}


class SubIn(BaseModel):
    subscription: dict


@router.post("/api/push/subscribe")
def push_subscribe(body: SubIn, user=Depends(auth.current_user)):
    save_subscription(user["id"], body.subscription)
    return {"ok": True}


@router.post("/api/push/test")
async def push_test(user=Depends(auth.current_user)):
    n = await notify(user["id"], "Ely", "Les notifications fonctionnent 🎉")
    return {"sent": n}
