"""Ely dans le Chrome de l'utilisateur : la vraie extension (dossier extension/) chargée dans Chromium,
reliée à un vrai serveur Ely, pilotée par l'outil `browser` comme le ferait l'agent."""
from __future__ import annotations

import asyncio
import io
import os
import socket
import zipfile
from pathlib import Path

import httpx
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
ENVOI = """<html><head><title>Envoi</title></head><body><h1>Joindre le dossier</h1>
  <input type="file" id="f" aria-label="Pièce jointe" onchange="const f = this.files[0];
    f.text().then(t => document.getElementById('r').textContent = 'reçu ' + f.name + ' (' + f.size + ' o) : ' + t)">
  <p id="r">rien</p></body></html>"""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def start_ely(port: int):
    app = create_app()
    for path, body in (("/test/doctolib", DOCTOLIB), ("/test/outlook", OUTLOOK), ("/test/aide", AIDE), ("/test/envoi", ENVOI)):
        app.add_api_route(path, lambda body=body: HTMLResponse(body), methods=["GET"])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, task


@pytest.fixture
async def ely_url():
    port = free_port()
    server, task = await start_ely(port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def launch_chrome(p, profile, url: str, token: str, ext: Path = EXT, type_url: bool = True):
    """Chrome de l'utilisateur avec l'extension, connecté à Ely (cookie de session) et réglé sur l'adresse `url`."""
    ctx = await p.chromium.launch_persistent_context(
        str(profile), executable_path=os.environ["ELY_BROWSER_EXECUTABLE"], headless=True,
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
    await ctx.add_cookies([{"name": "ely_token", "value": token, "url": url}])
    sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
    if type_url:
        await sw.evaluate(f"chrome.storage.local.set({{url: '{url}'}})")  # adresse saisie dans la fenêtre de l'extension
    return ctx


async def wait_bridge(user_id: int, seconds: float) -> bool:
    for _ in range(int(seconds * 10)):
        if chrome.bridges.get(user_id):
            return True
        await asyncio.sleep(0.1)
    return False


@pytest.fixture
async def users_chrome(ely_url, user, tmp_path):
    """Le Chrome de l'utilisateur, avec l'extension installée et connecté à Ely."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx = await launch_chrome(p, tmp_path / "profil", ely_url, auth.create_session(user["id"]))
        assert await wait_bridge(user["id"], 10), "l'extension ne s'est pas connectée à Ely"
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


async def test_extension_reconnects_when_ely_starts_after_chrome(user, tmp_path):
    """Ely redémarre (mise à jour) pendant que Chrome reste ouvert : l'extension se relie seule, vite."""
    from playwright.async_api import async_playwright

    port = free_port()
    async with async_playwright() as p:
        ctx = await launch_chrome(p, tmp_path / "profil", f"http://127.0.0.1:{port}", auth.create_session(user["id"]))
        try:
            await asyncio.sleep(1.5)  # Ely est encore arrêté
            server, task = await start_ely(port)
            try:
                assert await wait_bridge(user["id"], 12), "l'extension ne s'est pas reconnectée après le démarrage d'Ely"
            finally:
                server.should_exit = True
                await task
        finally:
            await ctx.close()
            chrome.bridges.pop(user["id"], None)


async def test_extension_waits_for_ely_before_opening_a_websocket(user, tmp_path):
    """Tant qu'Ely ne répond pas, l'extension se contente de sonder : aucune WebSocket vouée à l'échec
    (chacune inscrirait une erreur dans chrome://extensions)."""
    from playwright.async_api import async_playwright

    requests: list[str] = []

    async def unavailable(reader, writer):  # un serveur qui répond « indisponible » à tout
        line = (await reader.readline()).decode(errors="replace")
        requests.append(line.split(" ")[1] if " " in line else line)
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    stub = await asyncio.start_server(unavailable, "127.0.0.1", 0)
    port = stub.sockets[0].getsockname()[1]
    async with async_playwright() as p:
        ctx = await launch_chrome(p, tmp_path / "profil", f"http://127.0.0.1:{port}", auth.create_session(user["id"]))
        try:
            await asyncio.sleep(4)
        finally:
            await ctx.close()
            stub.close()
    assert any(r.startswith("/api/setup") for r in requests), requests
    assert not any(r.startswith("/api/chrome/ws") for r in requests), requests


async def test_ely_attaches_a_file_from_its_workspace_in_the_users_chrome(ely_url, users_chrome, user):
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    await execute(ctx, "file_write", {"path": "Promotion du livre.md", "content": "# S'envoler\nClubs de lecture de Vienne"})
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/envoi"})
    field = int(next(line for line in r.content.splitlines() if "Pièce jointe" in line).split("]")[0][1:])
    r = await execute(ctx, "browser", {"action": "upload", "ref": field, "text": "Promotion du livre.md"})
    assert not r.is_error, r.content
    assert "reçu Promotion du livre.md (" in r.content and "Clubs de lecture de Vienne" in r.content, r.content


async def test_upload_still_works_when_chrome_refuses_local_paths(ely_url, users_chrome, user, monkeypatch):
    """Chrome récent : une extension sans « accès aux URL de fichiers » ne peut pas donner un chemin local à un champ
    de fichier (et un Chrome sur une autre machine ne verrait pas ce chemin). Le fichier passe alors par la liaison."""
    real = chrome.ChromePage.cdp

    async def refusing(self, method, timeout=30, **params):
        if method == "DOM.setFileInputFiles":
            raise chrome.ChromeError("Not allowed")
        return await real(self, method, timeout, **params)

    monkeypatch.setattr(chrome.ChromePage, "cdp", refusing)
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    await execute(ctx, "file_write", {"path": "clubs.csv", "content": "club;ville\nLes Liseurs;Vienne"})
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/envoi"})
    field = int(next(line for line in r.content.splitlines() if "Pièce jointe" in line).split("]")[0][1:])
    r = await execute(ctx, "browser", {"action": "upload", "ref": field, "text": "clubs.csv"})
    assert not r.is_error, r.content
    assert "reçu clubs.csv (" in r.content and "Les Liseurs;Vienne" in r.content, r.content


async def test_extension_downloaded_from_ely_connects_without_typing_the_address(ely_url, user, tmp_path):
    """Menu du compte → Extension Chrome → Télécharger : décompressée et chargée, elle se relie seule à cet Ely."""
    from playwright.async_api import async_playwright

    token = auth.create_session(user["id"])
    async with httpx.AsyncClient(base_url=ely_url, cookies={"ely_token": token}) as c:
        r = await c.get("/api/chrome/extension.zip", params={"url": ely_url})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(tmp_path / "telechargements")
    ext = tmp_path / "telechargements" / "ely-chrome"
    assert (ext / "manifest.json").exists()
    async with async_playwright() as p:
        ctx = await launch_chrome(p, tmp_path / "profil", ely_url, token, ext=ext, type_url=False)
        try:
            assert await wait_bridge(user["id"], 10), "l'extension téléchargée ne s'est pas reliée à Ely"
        finally:
            await ctx.close()
            chrome.bridges.pop(user["id"], None)
