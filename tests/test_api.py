"""API HTTP : comptes, invitations, conversation, fichiers, mémoire, connexions, administration."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from ely import __version__
from ely.api.app import create_app
from ely.db import db

app = create_app()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ely.test") as c:
        yield c


async def login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_accounts_and_invites(client, user):
    admin = await login(client, user["email"], "motdepasse")
    r = await client.post("/api/auth/login", json={"email": user["email"], "password": "faux"})
    assert r.status_code == 401
    r = await client.post("/api/auth/register", json={"email": "intrus@x.fr", "password": "123456"})
    assert r.status_code == 403  # inscription fermée sans invitation
    code = (await client.post("/api/admin/invites", headers=admin)).json()["code"]
    r = await client.post("/api/auth/register", json={"email": "marie@x.fr", "name": "Marie", "password": "123456", "invite": code})
    assert r.status_code == 200 and r.json()["user"]["role"] == "user"
    marie = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/api/admin/users", headers=marie)).status_code == 403
    me = (await client.get("/api/me", headers=marie)).json()
    assert me["name"] == "Marie"
    r = await client.patch("/api/me", headers=marie, json={"settings": {"timezone": "Europe/Brussels", "voice": True}})
    assert r.json()["settings"]["timezone"] == "Europe/Brussels"
    client.cookies.clear()
    assert (await client.get("/api/me")).status_code == 401


async def test_chat_flow(client, user, fake):
    fake.script = lambda model, system, messages, tools: "Bonjour Franck !"
    h = await login(client, user["email"], "motdepasse")
    r = await client.post("/api/chat", headers=h, json={"text": "salut"})
    assert r.status_code == 200, r.text
    cid = r.json()["conversation_id"]
    for _ in range(50):
        await asyncio.sleep(0.05)
        data = (await client.get(f"/api/conversations/{cid}/messages", headers=h)).json()
        if len(data["messages"]) >= 2:
            break
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][1]["content"] == "Bonjour Franck !"
    convs = (await client.get("/api/conversations", headers=h)).json()
    assert any(c["id"] == cid for c in convs)
    found = (await client.get("/api/conversations", params={"q": "Bonjour"}, headers=h)).json()
    assert any(c["id"] == cid for c in found)
    r = await client.patch(f"/api/conversations/{cid}", headers=h, json={"title": "Test", "pinned": True})
    assert r.json()["title"] == "Test"
    assert (await client.delete(f"/api/conversations/{cid}", headers=h)).json()["ok"]


async def test_files(client, user):
    h = await login(client, user["email"], "motdepasse")
    r = await client.post("/api/files/upload", headers=h, files={"file": ("facture.txt", b"Total : 42 EUR", "text/plain")})
    path = r.json()["path"]
    assert path == "Reçus/facture.txt"
    assert any(f["path"] == path for f in (await client.get("/api/files", headers=h)).json())
    r = await client.get(f"/files/{path}", headers=h)
    assert r.text == "Total : 42 EUR"
    assert (await client.get("/files/../../ely.db", headers=h)).status_code in (400, 404)


async def test_elys_working_files_stay_out_of_the_users_files(client, user):
    """Les scripts et essais intermédiaires d'Ely ne s'affichent pas parmi les documents de l'utilisateur."""
    from conftest import new_conversation

    from ely.tools import ToolContext, execute

    h = await login(client, user["email"], "motdepasse")
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "file_write", {"path": ".travail/injection.js", "content": "document.title"})
    assert not r.is_error and r.files == []
    r = await execute(ctx, "file_write", {"path": "Promotion.md", "content": "# Promotion"})
    assert r.files == ["Promotion.md"]
    shown = [f["path"] for f in (await client.get("/api/files", headers=h)).json()]
    assert "Promotion.md" in shown and not any("injection" in f for f in shown)


async def test_memory_and_integrations(client, user):
    h = await login(client, user["email"], "motdepasse")
    await client.put("/api/memory/profile", headers=h, json={"content": "## Identité\n- Franck, développeur"})
    await client.post("/api/memory", headers=h, json={"content": "Franck préfère les rendez-vous le matin", "category": "préférence"})
    mem = (await client.get("/api/memory", headers=h)).json()
    assert "développeur" in mem["profile"] and any("matin" in m["content"] for m in mem["memories"])
    integ = (await client.get("/api/integrations", headers=h)).json()
    assert integ["google"]["connected"] is False and integ["ics_url"].endswith(".ics")
    token = integ["ics_url"].rsplit("/", 1)[1]
    r = await client.get(f"/ics/{token}")
    assert r.status_code == 200 and "BEGIN:VCALENDAR" in r.text
    r = await client.post("/api/credentials", headers=h, json={"service": "Doctolib", "username": "f", "password": "p"})
    assert r.status_code == 200
    creds = (await client.get("/api/credentials", headers=h)).json()
    assert creds[0]["service"] == "Doctolib" and "password" not in creds[0]


async def test_admin_models_and_selfdev(client, user, fake):
    h = await login(client, user["email"], "motdepasse")
    data = (await client.get("/api/models", headers=h)).json()
    assert data["roles"]["main"]["effective"] == "fake:agent"
    r = await client.put("/api/admin/models", headers=h, json={"main": "fake:fast"})
    assert r.json()["main"]["effective"] == "fake:fast"
    db.set_setting("model_main", "fake:agent")
    usage = (await client.get("/api/admin/usage", headers=h)).json()
    assert "rows" in usage
    sd = (await client.get("/api/admin/selfdev", headers=h)).json()
    assert "journal" in sd and "metrics" in sd
    health = (await client.get("/api/health")).json()
    assert health["ok"] and health["tools"] >= 25


async def test_favicon_is_the_current_icon(client):
    from pathlib import Path

    r = await client.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content == (Path(__file__).resolve().parent.parent / "ely/web/icons/favicon-32.png").read_bytes()
    page = (await client.get("/")).text
    assert 'href="/static/icons/favicon-32.png?v=' in page  # adresse versionnée : les navigateurs rechargent l'icône


async def test_after_an_update_the_browser_gets_the_new_interface(client):
    """Les fichiers de l'interface sont revalidés à chaque chargement : jamais d'ancienne version tirée du cache
    après `git pull` ; et la version qui tourne réellement est affichée (menu du compte)."""
    r = await client.get("/static/js/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    r2 = await client.get("/static/js/app.js", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304  # inchangé : rien n'est retéléchargé
    version = (await client.get("/api/setup")).json()["version"]
    assert version and version == (await client.get("/api/health")).json()["code"]
    assert version.startswith(f"{__version__} · "), version  # « 4.0.0 · a22ffa7 (26/09/2026) »


async def test_recorded_voice_is_relayed_from_the_macs_voice_service(client, user, xtts, monkeypatch):
    """La voix clonée de l'ancienne version (service XTTS du Mac) : Ely la propose et relaie la synthèse."""
    from ely.config import settings

    h = await login(client, user["email"], "motdepasse")
    assert (await client.get("/api/tts/voices", headers=h)).json() == {"voices": ["gert"], "default": "gert"}
    r = await client.post("/api/tts", headers=h, json={"text": "Votre  train part\nà 8 h 12.", "voice": "gert"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert xtts == [{"text": "Votre train part à 8 h 12.", "voice": "gert", "language": "fr"}]
    # service arrêté : aucune voix enregistrée proposée, et la page se rabat sur les voix du navigateur
    monkeypatch.setattr(settings, "xtts_url", "http://127.0.0.1:9")
    assert (await client.get("/api/tts/voices", headers=h)).json()["voices"] == []
    assert (await client.post("/api/tts", headers=h, json={"text": "Bonjour."})).status_code == 502


async def test_downloaded_extension_leaves_out_the_store_notes(client, user):
    """Le zip de l'extension n'emporte que l'extension : ni CHROMEWEBSTORE.md ni fichiers cachés."""
    import io
    import zipfile

    h = await login(client, user["email"], "motdepasse")
    r = await client.get("/api/chrome/extension.zip", headers=h, params={"url": "http://ely.test"})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "ely-chrome/manifest.json" in names and "ely-chrome/config.json" in names
    assert not [n for n in names if n.endswith(".md") or "/." in n], names


async def test_self_improvement_session_starts_from_settings(client, user, fake):
    """Réglages → Auto-amélioration → « Lancer » : la session démarre vraiment et la conversation est renvoyée."""
    from conftest import wait_idle

    fake.script = lambda model, system, messages, tools: "Rien à améliorer aujourd'hui."
    h = await login(client, user["email"], "motdepasse")
    r = await client.post("/api/admin/selfdev/run", headers=h, json={"goal": "Sois plus rapide sur Doctolib"})
    assert r.status_code == 200, r.text
    cid = r.json()["conversation_id"]
    await wait_idle(cid)
    run = db.one("SELECT * FROM runs WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    assert run and run["objective"] == "Sois plus rapide sur Doctolib" and run["status"] == "done"


async def test_password_guessing_is_slowed_down_without_locking_franck_out(user, monkeypatch):
    """Ely est souvent joignable depuis Internet. Un robot qui devine le mot de passe de Franck est bloqué un quart
    d'heure après 5 essais (même s'il tombe ensuite sur le bon) ; Franck, lui, se connecte normalement depuis chez
    lui et il est prévenu des essais. Une adresse qui essaie tous les comptes est bloquée pour tous."""
    from ely import auth
    from ely import notify as notify_module
    from ely.api import chat

    monkeypatch.setattr(chat, "_failures", {})
    monkeypatch.setattr(chat, "FAIL_DELAY", 0)
    alerts = []

    async def notify(user_id, title, body, **kw):
        alerts.append((user_id, body))

    monkeypatch.setattr(notify_module, "notify", notify)

    def client_from(ip: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 4000)), base_url="http://ely.test")

    async with client_from("198.51.100.7") as robot, client_from("203.0.113.20") as home:
        for _ in range(5):
            r = await robot.post("/api/auth/login", json={"email": user["email"], "password": "devine"})
            assert r.status_code == 401
        r = await robot.post("/api/auth/login", json={"email": user["email"], "password": "motdepasse"})
        assert r.status_code == 429 and "réessayez dans 15 min" in r.json()["detail"]
        r = await home.post("/api/auth/login", json={"email": user["email"], "password": "motdepasse"})
        assert r.status_code == 200  # Franck n'est pas enfermé dehors
        await asyncio.sleep(0.05)
        assert len(alerts) == 1 and alerts[0][0] == user["id"] and "198.51.100.7" in alerts[0][1]

        others = [auth.create_user(f"cible{i}{time.time_ns()}@x.fr", "Cible", "123456") for i in range(4)]
        for target in others:
            for _ in range(4):
                await robot.post("/api/auth/login", json={"email": target["email"], "password": "devine"})
        r = await robot.post("/api/auth/login", json={"email": others[0]["email"], "password": "123456"})
        assert r.status_code == 429  # 20 échecs depuis cette adresse, tous comptes confondus

        for k in chat._failures:  # le délai passé, le bon mot de passe fonctionne de nouveau
            chat._failures[k] = [t - chat.LOGIN_WINDOW for t in chat._failures[k]]
        r = await robot.post("/api/auth/login", json={"email": user["email"], "password": "motdepasse"})
        assert r.status_code == 200 and not chat._failures  # les essais périmés sont oubliés


async def test_login_time_does_not_reveal_who_has_an_account(user, monkeypatch):
    """Un e-mail inconnu coûte le même calcul de mot de passe qu'un compte existant."""
    from ely import auth
    from ely.api import chat

    monkeypatch.setattr(chat, "_failures", {})
    monkeypatch.setattr(chat, "FAIL_DELAY", 0)
    checked = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: checked.append(h) or real(p, h))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ely.test") as c:
        await c.post("/api/auth/login", json={"email": "personne@nulle.part", "password": "devine"})
        await c.post("/api/auth/login", json={"email": user["email"], "password": "devine"})
    assert len(checked) == 2 and checked[0] == auth.DUMMY_HASH


def test_session_tokens_never_reach_the_server_logs():
    """La session de l'extension Chrome passe dans l'adresse du WebSocket : elle ne doit pas s'écrire dans la console
    (qui finit dans un fichier, une capture, un message…). Idem pour le jeton du flux iCal."""
    import logging

    create_app()
    secret = "9A-pL4h4w4KK4VI-Ite1Gr23gwbwctmiytjU5C0akyg"
    lines: list[str] = []
    seen = logging.Handler()
    seen.emit = lambda record: lines.append(record.getMessage())
    loggers = [logging.getLogger(n) for n in ("uvicorn.error", "uvicorn.access")]  # uvicorn ne les fait pas remonter
    for lg in loggers:
        lg.addHandler(seen)
        lg.setLevel(logging.INFO)
    try:
        loggers[0].info('%s - "WebSocket %s" [accepted]', "203.0.113.5:0", f"/api/chrome/ws?token={secret}")
        loggers[1].info('%s - "%s %s HTTP/%s" %d', "203.0.113.5:0", "GET", "/ics/AbC_dEf-123.ics", "1.1", 200)
    finally:
        for lg in loggers:
            lg.removeHandler(seen)
    text = "\n".join(lines)
    assert secret not in text and "AbC_dEf-123" not in text
    assert "/api/chrome/ws?token=•••" in text and "/ics/•••.ics" in text


async def test_reasoning_effort_follows_each_role(client, user, fake):
    """Demandes courantes en effort Moyen, escalade et auto-amélioration en Élevé. L'admin règle chaque rôle, effectif
    dès la tâche suivante ; un compte ordinaire ne le peut pas, et un effort inconnu est refusé."""
    from conftest import wait_idle

    from ely import auth
    from ely.llm import registry

    fake.script = lambda model, system, messages, tools: "C'est fait."
    h = await login(client, user["email"], "motdepasse")

    async def agent_effort(text: str) -> str:
        fake.calls.clear()
        cid = (await client.post("/api/chat", headers=h, json={"text": text})).json()["conversation_id"]
        await wait_idle(cid)
        return next(c["effort"] for c in fake.calls if c["tools"])  # l'appel de l'agent, avec ses outils

    roles = (await client.get("/api/models", headers=h)).json()["roles"]
    assert [roles[r]["effort"] for r in ("main", "strong", "selfdev")] == ["medium", "high", "high"]
    assert await agent_effort("Quelles sont les nouvelles du jour ?") == "medium"
    try:
        r = await client.put("/api/admin/models", headers=h, json={"effort": {"main": "high"}})
        assert r.status_code == 200 and r.json()["main"]["effort"] == "high"
        assert await agent_effort("Vérifie mes e-mails") == "high"
        await registry.chat(role="selfdev", system=["s"], messages=[{"role": "user", "content": "Améliore-toi"}])
        assert fake.calls[-1]["effort"] == "high"
        await registry.complete("Donne un titre", role="local")
        assert fake.calls[-1]["effort"] == "low"  # tâches de fond : toujours l'effort le plus faible
        for bad in ({"main": "extreme"}, {"fast": "high"}):
            assert (await client.put("/api/admin/models", headers=h, json={"effort": bad})).status_code == 400
        member = auth.create_user("lea.effort@x.fr", "Léa", "motdepasse")
        db.run("UPDATE users SET role = 'user' WHERE id = ?", (member["id"],))
        r = await client.put("/api/admin/models", json={"effort": {"main": "medium"}},
                             headers={"Authorization": f"Bearer {auth.create_session(member['id'])}"})
        assert r.status_code == 403 and registry.effort("main") == "high"
    finally:
        db.set_setting("effort_main", "medium")
