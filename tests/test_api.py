"""API HTTP : comptes, invitations, conversation, fichiers, mémoire, connexions, administration."""
from __future__ import annotations

import asyncio

import httpx
import pytest

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
