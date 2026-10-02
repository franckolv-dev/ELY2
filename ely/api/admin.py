"""Routes d'administration : utilisateurs, modèles, consommation, auto-amélioration."""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth
from ..config import settings
from ..db import db, now
from ..llm import registry
from ..llm.registry import ROLES, price_of
from ..selfdev import metrics, pipeline, plugins

router = APIRouter()


@router.get("/api/models")
async def models(user=Depends(auth.current_user)):
    await registry.ensure_catalog()
    return {"models": [m for m in registry.all_models() if m["kind"] == "llm"], "roles": registry.roles_view(),
            "providers": registry.status}


@router.post("/api/admin/models/refresh")
async def refresh_models(user=Depends(auth.admin_user)):
    from ..config import reload_env

    reload_env()  # nouvelles clés du .env prises en compte sans redémarrer
    registry.build_providers()
    await registry.refresh()
    return {"providers": registry.status, "roles": registry.roles_view()}


@router.get("/api/admin/models/all")
async def all_models(user=Depends(auth.admin_user)):
    await registry.ensure_catalog()
    return registry.all_models()


class RolesIn(BaseModel):
    main: str | None = None
    strong: str | None = None
    selfdev: str | None = None
    fast: str | None = None
    local: str | None = None
    embed: str | None = None
    fallbacks: str | None = None


@router.put("/api/admin/models")
def set_roles(body: RolesIn, user=Depends(auth.admin_user)):
    for role in (*ROLES, "fallbacks"):
        v = getattr(body, role)
        if v is not None:
            db.set_setting(f"model_{role}", v.strip() or "auto")
    return registry.roles_view()


# ---------------------------------------------------------------------- utilisateurs
@router.get("/api/admin/users")
def users(user=Depends(auth.admin_user)):
    return db.all("SELECT u.id, u.email, u.name, u.role, u.created_at, "
                  "(SELECT MAX(last_seen) FROM sessions s WHERE s.user_id = u.id) AS last_seen, "
                  "(SELECT COUNT(*) FROM runs r WHERE r.user_id = u.id) AS runs FROM users u ORDER BY u.id")


class UserIn(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "user"


@router.post("/api/admin/users")
def create_user(body: UserIn, user=Depends(auth.admin_user)):
    return auth.create_user(body.email, body.name, body.password, role=body.role if body.role in ("admin", "user") else "user")


class UserPatch(BaseModel):
    role: str | None = None
    password: str | None = None
    name: str | None = None


@router.patch("/api/admin/users/{uid}")
async def patch_user(uid: int, body: UserPatch, user=Depends(auth.admin_user)):
    if body.role in ("admin", "user"):
        if uid == user["id"] and body.role != "admin":
            raise HTTPException(400, "Vous ne pouvez pas vous retirer vous-même le rôle administrateur")
        db.run("UPDATE users SET role = ? WHERE id = ?", (body.role, uid))
    if body.password:  # mot de passe réinitialisé par l'administrateur : toutes les sessions du compte sont fermées
        from .. import chrome

        auth.set_password(uid, body.password)
        await chrome.disconnect(uid)
    if body.name:
        db.run("UPDATE users SET name = ? WHERE id = ?", (body.name, uid))
    return auth.get_user(uid)


@router.delete("/api/admin/users/{uid}")
async def delete_user(uid: int, user=Depends(auth.admin_user)):
    """Tout ce qui appartient au compte disparaît avec lui : un compte créé ensuite peut reprendre le même numéro, il ne
    doit rien en hériter (fichiers, profil du navigateur et ses connexions, compétences, statistiques)."""
    if uid == user["id"]:
        raise HTTPException(400, "Impossible de supprimer votre propre compte")
    import shutil

    from .. import chrome
    from ..agent.runner import runner
    from ..browser import manager
    from ..memory.store import purge_user_index

    for cid in [s.conversation_id for s in list(runner.states.values()) if s.user_id == uid]:
        await runner.stop(cid)
    await chrome.disconnect(uid)
    await manager.close_user(uid)
    purge_user_index(uid)
    for table in ("runs", "usage", "tool_log"):  # sans clé étrangère vers users
        db.run(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
    db.run("DELETE FROM users WHERE id = ?", (uid,))
    shutil.rmtree(settings.data_dir / "users" / str(uid), ignore_errors=True)
    return {"ok": True}


@router.post("/api/admin/invites")
def create_invite(user=Depends(auth.admin_user)):
    code = secrets.token_urlsafe(6)
    db.insert("invites", code=code, created_by=user["id"], created_at=now())
    return {"code": code}


@router.get("/api/admin/invites")
def invites(user=Depends(auth.admin_user)):
    return db.all("SELECT * FROM invites ORDER BY created_at DESC LIMIT 50")


# ---------------------------------------------------------------------- consommation
@router.get("/api/admin/usage")
def usage(days: float = 30, user=Depends(auth.admin_user)):
    rows = db.all("SELECT COALESCE(u.name, 'système') AS user, g.model, g.purpose, SUM(g.input_tokens) AS input, "
                  "SUM(g.output_tokens) AS output, SUM(g.cached_tokens) AS cached, COUNT(*) AS calls FROM usage g "
                  "LEFT JOIN users u ON u.id = g.user_id WHERE g.created_at > ? GROUP BY user, g.model, g.purpose "
                  "ORDER BY input DESC", (time.time() - days * 86400,))
    total = 0.0
    for r in rows:
        pi, po = price_of(r["model"])
        r["cost"] = round(((r["input"] or 0) - (r["cached"] or 0) * 0.9) / 1e6 * pi + (r["output"] or 0) / 1e6 * po, 4)
        total += r["cost"]
    return {"rows": rows, "total_cost": round(total, 3)}


# ---------------------------------------------------------------------- auto-amélioration
@router.get("/api/admin/selfdev")
def selfdev_state(user=Depends(auth.admin_user)):
    return {"auto": db.get_setting("selfdev_auto", True), "hour": db.get_setting("selfdev_hour", 4),
            "guidelines": db.get_setting("learned_guidelines", ""), "plugins": plugins.list_plugins(),
            "journal": db.all("SELECT id, kind, title, detail, commit_sha, status, created_at, length(diff) AS diff_size "
                              "FROM improvements ORDER BY id DESC LIMIT 100"),
            "metrics": metrics.collect(7)}


@router.get("/api/admin/improvements/{iid}")
def improvement(iid: int, user=Depends(auth.admin_user)):
    return db.one("SELECT * FROM improvements WHERE id = ?", (iid,))


class SelfdevSettings(BaseModel):
    auto: bool | None = None
    hour: int | None = None
    guidelines: str | None = None


@router.put("/api/admin/selfdev")
def selfdev_settings(body: SelfdevSettings, user=Depends(auth.admin_user)):
    if body.auto is not None:
        db.set_setting("selfdev_auto", body.auto)
    if body.hour is not None:
        db.set_setting("selfdev_hour", max(0, min(23, body.hour)))
    if body.guidelines is not None:
        db.set_setting("learned_guidelines", body.guidelines)
    return {"ok": True}


class GoalIn(BaseModel):
    goal: str = ""


@router.post("/api/admin/selfdev/run")
async def selfdev_run(body: GoalIn, user=Depends(auth.admin_user)):  # async : la session démarre dans la boucle du serveur
    from ..selfdev.tools import start_session

    return {"conversation_id": start_session(user, body.goal)}


class PluginToggle(BaseModel):
    enabled: bool


@router.post("/api/admin/plugins/{name}")
def plugin_toggle(name: str, body: PluginToggle, user=Depends(auth.admin_user)):
    plugins.set_disabled(name, not body.enabled)
    if body.enabled:
        plugins.load_one(name)
    else:
        plugins._unload(name)
    return {"ok": True}


@router.post("/api/admin/improvements/{iid}/revert")
async def revert(iid: int, user=Depends(auth.admin_user)):
    imp = db.one("SELECT * FROM improvements WHERE id = ?", (iid,))
    if not imp or imp["kind"] != "code" or not imp["commit_sha"]:
        raise HTTPException(400, "Seules les modifications de code déployées peuvent être annulées ici")
    code, out = await pipeline.git("-c", "user.name=Ely", "-c", "user.email=ely@localhost", "revert", "--no-edit", imp["commit_sha"])
    if code:  # conflit avec une modification plus récente : le code reste tel quel, sans marqueurs de conflit
        await pipeline.git("revert", "--abort")
        raise HTTPException(400, f"Annulation impossible (modifiée depuis par une autre amélioration ?) : {out[-800:]}")
    db.update("improvements", "id = ?", (iid,), status="reverted")
    import os

    if os.environ.get("ELY_SUPERVISED") == "1":
        pipeline.restart_soon(2)
        return {"ok": True, "message": "Modification annulée, redémarrage…"}
    return {"ok": True, "message": "Modification annulée. Redémarre Ely pour l'appliquer."}


# ---------------------------------------------------------------------- extensions MCP
@router.get("/api/admin/mcp")
def mcp_state(user=Depends(auth.admin_user)):
    from ..mcp_client import load_config, manager

    return {"servers": load_config(), "status": manager.status()}


class McpIn(BaseModel):
    servers: dict


@router.put("/api/admin/mcp")
async def mcp_save(body: McpIn, user=Depends(auth.admin_user)):
    from ..mcp_client import manager, save_config

    save_config(body.servers)
    await manager.start_all()
    return {"status": manager.status()}


# ---------------------------------------------------------------------- abonnement ChatGPT
@router.get("/api/admin/chatgpt")
def chatgpt_state(user=Depends(auth.admin_user)):
    from ..llm import chatgpt_provider

    return chatgpt_provider.status()


class ChatGPTIn(BaseModel):
    auth_json: str = ""


@router.post("/api/admin/chatgpt")
async def chatgpt_import(body: ChatGPTIn, user=Depends(auth.admin_user)):
    from ..llm import LLMError, chatgpt_provider

    try:
        st = await chatgpt_provider.import_auth(body.auth_json.strip() or None)
    except LLMError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, f"Contenu illisible : {e}")
    registry.build_providers()
    await registry.refresh()
    return {**st, "roles": registry.roles_view()}


@router.delete("/api/admin/chatgpt")
async def chatgpt_disconnect(user=Depends(auth.admin_user)):
    from ..llm import chatgpt_provider

    chatgpt_provider.disconnect()
    registry.build_providers()
    await registry.refresh()
    return {"ok": True}


# ---------------------------------------------------------------------- Gemini par l'abonnement Google (CLI gemini)
@router.get("/api/admin/geminicli")
async def geminicli_state(user=Depends(auth.admin_user)):
    from ..llm import gemini_cli

    return await gemini_cli.status()


class GeminiCLIIn(BaseModel):
    enabled: bool


@router.put("/api/admin/geminicli")
async def geminicli_toggle(body: GeminiCLIIn, user=Depends(auth.admin_user)):
    from ..llm import gemini_cli

    db.set_setting(gemini_cli.SETTING, body.enabled)
    registry.build_providers()
    await registry.refresh()
    return {**await gemini_cli.status(), "roles": registry.roles_view()}


@router.post("/api/admin/geminicli/test")
async def geminicli_test(user=Depends(auth.admin_user)):
    from ..llm import gemini_cli

    return await gemini_cli.ping()


# ---------------------------------------------------------------------- Claude par l'Agent SDK
@router.get("/api/admin/claude")
def claude_state(user=Depends(auth.admin_user)):
    from ..llm import claude_agent

    return claude_agent.status()


class ClaudeIn(BaseModel):
    budget: float | None = None


@router.put("/api/admin/claude")
def claude_settings(body: ClaudeIn, user=Depends(auth.admin_user)):
    from ..llm import claude_agent

    if body.budget is not None:
        db.set_setting("claude_budget", max(0.1, min(100.0, body.budget)))
    return claude_agent.status()


class ClaudeTestIn(BaseModel):
    model: str = ""


@router.post("/api/admin/claude/test")
async def claude_test(body: ClaudeTestIn, user=Depends(auth.admin_user)):
    from ..llm import claude_agent

    st = claude_agent.status()
    if not st["ready"]:
        raise HTTPException(400, "Claude n'est pas prêt : installez le SDK et ajoutez ANTHROPIC_API_KEY dans .env")
    try:
        return await claude_agent.ping(body.model if claude_agent.is_claude(body.model) else claude_agent.DEFAULT)
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
