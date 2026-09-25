"""Ely dans le Chrome de l'utilisateur : la vraie extension (dossier extension/) chargée dans Chromium,
reliée à un vrai serveur Ely, pilotée par l'outil `browser` comme le ferait l'agent."""
from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import pytest
import uvicorn
import websockets
from conftest import new_conversation
from fastapi.responses import HTMLResponse

from ely import auth, chrome
from ely.api.app import create_app
from ely.browser import manager
from ely.tools import ToolContext, execute

EXT = Path(__file__).resolve().parent.parent / "extension"
pytestmark = pytest.mark.skipif(not os.environ.get("ELY_BROWSER_EXECUTABLE"), reason="Chromium absent")

DOCTOLIB = """<html><head><title>Doctolib</title></head><body>
  <h1>Vérification de connexion</h1>
  <label for="c">Code reçu par e-mail</label><input id="c" autocomplete="one-time-code">
  <button onclick="const ok = document.getElementById('c').value === '482913';
    document.body.innerHTML = ok ? '<h1>Prochain rendez-vous : jeudi 2 octobre à 17 h 30</h1><p>' + (event.isTrusted ? 'clic réel' : 'clic simulé') + '</p>'
                                 : '<h1>Code incorrect</h1>'">Valider</button>
  <a href="/test/aide" target="_blank">Aide</a>
</body></html>"""
OUTLOOK = """<html><head><title>Outlook</title></head><body><h1>Boîte de réception</h1>
  <p>Doctolib — Votre code de vérification est 482913</p></body></html>"""
AIDE = "<html><head><title>Aide Doctolib</title></head><body><h1>Centre d'aide</h1></body></html>"


@pytest.fixture
async def ely_url():
    app = create_app()
    for path, body in (("/test/doctolib", DOCTOLIB), ("/test/outlook", OUTLOOK), ("/test/aide", AIDE)):
        app.add_api_route(path, lambda body=body: HTMLResponse(body), methods=["GET"])
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


@pytest.fixture
async def users_chrome(ely_url, user, tmp_path):
    """Le Chrome de l'utilisateur, avec l'extension installée et connecté à Ely."""
    from playwright.async_api import async_playwright

    token = auth.create_session(user["id"])
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profil"), executable_path=os.environ["ELY_BROWSER_EXECUTABLE"], headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
        await ctx.add_cookies([{"name": "ely_token", "value": token, "url": ely_url}])  # connecté à Ely dans ce Chrome
        sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
        await sw.evaluate(f"chrome.storage.local.set({{url: '{ely_url}'}})")  # adresse saisie dans la fenêtre de l'extension
        for _ in range(100):
            if chrome.bridges.get(user["id"]):
                break
            await asyncio.sleep(0.1)
        assert chrome.bridges.get(user["id"]), "l'extension ne s'est pas connectée à Ely"
        yield ctx
        await ctx.close()


async def test_ely_reads_the_mfa_code_in_the_users_webmail(ely_url, users_chrome, user):
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))

    async def browser(**args):
        r = await execute(ctx, "browser", args)
        assert not r.is_error, r.content
        return r

    def ref(snapshot: str, label: str) -> int:
        return int(next(line for line in snapshot.splitlines() if label in line).split("]")[0][1:])

    r = await browser(action="open", url=f"{ely_url}/test/doctolib")
    assert "Code reçu par e-mail" in r.content
    code_field = ref(r.content, 'champ(text) "Code reçu par e-mail"')

    # le code arrive sur la messagerie déjà connectée dans Chrome : Ely l'ouvre dans un autre onglet
    r = await browser(action="open", url=f"{ely_url}/test/outlook", new_tab=True)
    assert "482913" in r.content and "2 onglets ouverts" in r.content
    r = await browser(action="tabs")
    assert "Doctolib" in r.content and "Outlook" in r.content
    r = await browser(action="switch_tab", tab=0)
    assert "Code reçu par e-mail" in r.content

    await browser(action="type", ref=code_field, text="482913")
    r = await browser(action="click", ref=ref(r.content, 'bouton "Valider"'))
    assert "Prochain rendez-vous : jeudi 2 octobre" in r.content and "clic réel" in r.content, r.content

    # un lien qui ouvre un nouvel onglet : Ely continue dans ce nouvel onglet
    await browser(action="back")
    r = await browser(action="open", url=f"{ely_url}/test/doctolib")
    r = await browser(action="click", ref=ref(r.content, 'lien "Aide"'))
    assert "Centre d'aide" in r.content, r.content

    r = await execute(ctx, "browser", {"action": "screenshot"})
    assert r.images
    assert manager.users.get(user["id"]) is None  # le navigateur interne n'a pas servi


async def test_internal_browser_when_chrome_is_set_aside_or_gone(users_chrome, user):
    assert isinstance(chrome.chrome_for(user["id"]), chrome.ChromeUserBrowser)
    auth.update_user_settings(user["id"], browser="interne")
    assert chrome.chrome_for(user["id"]) is None
    auth.update_user_settings(user["id"], browser="chrome")
    assert chrome.chrome_for(user["id"]) is not None
    await users_chrome.close()  # Chrome quitté
    for _ in range(50):
        if not chrome.bridges.get(user["id"]):
            break
        await asyncio.sleep(0.1)
    assert chrome.chrome_for(user["id"]) is None


async def test_extension_needs_a_valid_ely_session(ely_url):
    with pytest.raises(websockets.exceptions.WebSocketException):
        async with websockets.connect(ely_url.replace("http", "ws") + "/api/chrome/ws?token=faux") as ws:
            await ws.recv()
