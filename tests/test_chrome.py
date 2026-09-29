"""Ely dans le Chrome de l'utilisateur : la vraie extension (dossier extension/) chargée dans Chromium,
reliée à un vrai serveur Ely, pilotée par l'outil `browser` comme le ferait l'agent."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
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
TENACE = "<html><head><title>Résultats</title></head><body data-tenace><h1>Résultats d'analyse</h1></body></html>"
# une page où l'extension « Coffre » ouvre sa page dans un cadre sans adresse visible (comme le lecteur PDF de Chrome)
LECTEUR = "<html><head><title>Ordonnance</title></head><body data-lecteur><h1>Ordonnance du 12 septembre</h1></body></html>"
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
    for path, body in (("/test/doctolib", DOCTOLIB), ("/test/outlook", OUTLOOK), ("/test/aide", AIDE), ("/test/envoi", ENVOI),
                       ("/test/lecteur", LECTEUR), ("/test/tenace", TENACE)):
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


async def launch_chrome(p, profile, url: str, token: str, ext: Path = EXT, type_url: bool = True, others: tuple = ()):
    """Chrome de l'utilisateur avec l'extension, connecté à Ely (cookie de session) et réglé sur l'adresse `url`.
    `others` : ses autres extensions."""
    exts = ",".join(str(e) for e in (ext, *others))
    ctx = await p.chromium.launch_persistent_context(
        str(profile), executable_path=os.environ["ELY_BROWSER_EXECUTABLE"], headless=True,
        args=[f"--disable-extensions-except={exts}", f"--load-extension={exts}"])
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


DEBUGGED = """async () => { const [t] = await chrome.tabs.query({ url: '*://*/test/aide' });
  try { await chrome.debugger.sendCommand({ tabId: t.id }, 'Runtime.evaluate', { expression: '1' }); return true; }
  catch { return false; } }"""


async def test_extension_lets_go_of_the_tabs_when_ely_goes_quiet(ely_url, users_chrome, user):
    """Sans commande d'Ely, l'extension détache ses onglets (la barre « débogue ce navigateur » disparaît), même
    ceux attachés avant un redémarrage de son service worker : ni la minuterie ni la liste ne vivent en mémoire."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/aide"})
    assert not r.is_error, r.content
    sw = users_chrome.service_workers[0]
    assert await sw.evaluate(DEBUGGED)
    assert await sw.evaluate("async () => !!(await chrome.alarms.get('detach'))")
    await sw.evaluate("attached.clear()")  # la mémoire du service worker, perdue à son redémarrage
    await sw.evaluate("chrome.alarms.create('detach', { when: Date.now() })")  # le délai d'inactivité est écoulé
    for _ in range(50):
        if not await sw.evaluate(DEBUGGED):
            break
        await asyncio.sleep(0.1)
    assert not await sw.evaluate(DEBUGGED)


async def test_extension_window_shows_the_link_with_ely(ely_url, users_chrome, user):
    """La fenêtre de l'icône dit que ce Chrome est relié à Ely, et à quelle adresse."""
    ext_id = users_chrome.service_workers[0].url.split("/")[2]
    page = await users_chrome.new_page()
    await page.goto(f"chrome-extension://{ext_id}/popup.html")
    await page.wait_for_selector("#dot.on")
    assert await page.input_value("#url") == ely_url
    await page.close()


async def test_stale_element_number_in_chrome_gives_the_current_page(ely_url, users_chrome, user, caplog):
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/doctolib"})
    old = int(next(line for line in r.content.splitlines() if 'bouton "Valider"' in line).split("]")[0][1:])
    await execute(ctx, "browser", {"action": "eval", "js": "document.body.innerHTML = '<h1>Session expirée</h1><button>Se reconnecter</button>'"})
    with caplog.at_level("ERROR", logger="ely.tools"):
        r = await execute(ctx, "browser", {"action": "click", "ref": old})
    assert r.is_error and f"[{old}] n'existe plus" in r.content and "Se reconnecter" in r.content, r.content
    assert not [rec for rec in caplog.records if rec.levelname == "ERROR"]


# un gestionnaire de mots de passe comme Passbolt : il glisse son menu, une page de l'extension cachée dans une
# racine fantôme fermée, dans chaque page qui a un champ de saisie, et le recrée à chaque focus, près du champ
MENU_JS = """
const show = () => {
  document.querySelector("coffre-menu")?.remove();
  if (!document.querySelector("input")) return;
  const host = document.createElement("coffre-menu");
  const frame = document.createElement("iframe");
  frame.src = chrome.runtime.getURL("menu.html");
  host.attachShadow({ mode: "closed" }).append(frame);
  document.body.append(host);
};
show();
document.addEventListener("focusin", show);
const load = () => {  // cadre chargé par script : son attribut src reste vide
  const frame = document.createElement("iframe");
  document.body.append(frame);
  frame.contentWindow.location.href = chrome.runtime.getURL("menu.html");
  return frame;
};
if (document.body.hasAttribute("data-lecteur")) load();
if (document.body.hasAttribute("data-tenace")) {  // remet son cadre dès qu'on le retire
  let frame = load();
  new MutationObserver(() => { if (!frame.isConnected) frame = load(); }).observe(document.body, { childList: true });
}
"""


def password_manager(folder: Path) -> Path:
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": "Coffre", "version": "1.0",
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["menu.js"], "run_at": "document_idle"}],
        "web_accessible_resources": [{"resources": ["menu.html"], "matches": ["<all_urls>"]}]}))
    (folder / "menu.js").write_text(MENU_JS)
    (folder / "menu.html").write_text("<p>Remplir avec Coffre</p>")
    return folder


def unpacked_id(folder: Path) -> str:
    """Identifiant que Chrome donne à une extension chargée depuis un dossier."""
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(str(folder).encode()).hexdigest()[:32])


@pytest.fixture
async def chrome_with_password_manager(ely_url, user, tmp_path):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        ctx = await launch_chrome(p, tmp_path / "profil", ely_url, auth.create_session(user["id"]),
                                  others=(password_manager(tmp_path / "coffre"),))
        assert await wait_bridge(user["id"], 10), "l'extension ne s'est pas connectée à Ely"
        yield ctx
        await ctx.close()
        chrome.bridges.pop(user["id"], None)
        chrome.browsers.pop(user["id"], None)


async def test_ely_keeps_control_when_another_extension_slips_its_menu_into_the_page(ely_url, chrome_with_password_manager, user):
    """Chrome interdit à une extension de piloter un onglet où se trouve une page d'une autre extension
    (« Cannot access a chrome-extension:// URL of different extension ») : le menu du gestionnaire de mots de passe
    ne doit pas priver Ely de l'onglet, ni au chargement de la page, ni quand il revient au focus d'un champ."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))

    async def browser(**args):
        r = await execute(ctx, "browser", args)
        assert not r.is_error, r.content
        return r

    r = await browser(action="open", url=f"{ely_url}/test/doctolib")
    assert "Code reçu par e-mail" in r.content
    field = int(next(line for line in r.content.splitlines() if 'champ(text) "Code reçu par e-mail"' in line).split("]")[0][1:])
    await browser(action="type", ref=field, text="482913")
    r = await browser(action="snapshot")
    button = int(next(line for line in r.content.splitlines() if 'bouton "Valider"' in line).split("]")[0][1:])
    r = await browser(action="click", ref=button)
    assert "Prochain rendez-vous : jeudi 2 octobre" in r.content and "clic réel" in r.content, r.content


async def test_ely_leaves_alone_a_tab_another_extension_opened_in_its_window(ely_url, chrome_with_password_manager, user, tmp_path):
    """Une extension ouvre sa page (nouveautés après une mise à jour…) dans la fenêtre d'Ely, puis Ely redémarre :
    Ely ne doit pas prendre cet onglet, qu'il ne peut pas piloter, pour le sien."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/aide"})
    assert not r.is_error, r.content
    sw = chrome_with_password_manager.service_workers[0]
    page = f"chrome-extension://{unpacked_id(tmp_path / 'coffre')}/menu.html"
    await sw.evaluate(f"""async () => {{ const {{ win }} = await chrome.storage.session.get("win");
      await chrome.tabs.create({{ windowId: win, index: 0, url: "{page}", active: true }}); }}""")
    chrome.browsers.pop(user["id"], None)  # redémarrage d'Ely : il ne sait plus quel onglet était le sien
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/doctolib"})
    assert not r.is_error and "Code reçu par e-mail" in r.content, r.content
    tabs = await sw.evaluate("async () => (await chrome.tabs.query({})).map((t) => t.url)")
    assert page in tabs, tabs  # l'onglet de l'autre extension est resté tel quel


async def test_ely_removes_an_extension_frame_loaded_by_script(ely_url, chrome_with_password_manager, user):
    """Le cadre d'une autre extension chargé par script n'a pas d'adresse dans la page (son attribut src reste vide) :
    Chrome le signale quand même à Ely, qui le retire et lit la page."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/lecteur"})
    assert not r.is_error and "Ordonnance du 12 septembre" in r.content, r.content


async def test_ely_is_never_stuck_on_a_page_it_cannot_drive(ely_url, chrome_with_password_manager, user):
    """Une extension remet son cadre dès qu'on le retire (ou Chrome affiche un PDF, page de son lecteur intégré) :
    l'onglet ne se pilote plus. Ely le dit clairement, et peut toujours ouvrir une autre page dans le même onglet."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=lambda *a: asyncio.sleep(0))
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/tenace"})
    assert "une autre extension" in r.content and "Cannot access" not in r.content, r.content
    r = await execute(ctx, "browser", {"action": "open", "url": f"{ely_url}/test/doctolib"})
    assert not r.is_error and "Code reçu par e-mail" in r.content, r.content
