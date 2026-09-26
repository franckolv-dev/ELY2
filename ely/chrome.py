"""Ely dans le Chrome de l'utilisateur, via l'extension « Ely pour Chrome » (dossier extension/).

L'extension ouvre une WebSocket vers Ely et exécute ses commandes dans une fenêtre
dédiée de Chrome, avec toutes les sessions de l'utilisateur (messagerie, Doctolib…).
Les actions passent par le protocole DevTools (chrome.debugger) : clics et frappes
réels, lecture de la page, captures. Côté Ely, ChromePage imite la petite partie de
l'API Playwright qu'utilise l'outil `browser` : l'outil marche à l'identique avec les
deux navigateurs, et le navigateur interne reste le secours quand Chrome est fermé.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import mimetypes
import re
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect

from . import auth
from .browser import BaseUserBrowser

log = logging.getLogger("ely.chrome")
router = APIRouter()
EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"


class ChromeError(Exception):
    pass


# ---------------------------------------------------------------------- liaison avec l'extension
class ChromeBridge:
    """Une extension connectée : envoie des commandes et attend leurs réponses."""

    def __init__(self, user_id: int, send) -> None:
        self.user_id = user_id
        self._send = send
        self.pending: dict[int, asyncio.Future] = {}
        self.seq = 0
        self.info: dict = {}
        self.closed = False

    @property
    def mac(self) -> bool:
        return "mac" in (self.info.get("platform", "") + self.info.get("ua", "")).lower()

    async def call(self, cmd: str, timeout: float = 30, **params):
        if self.closed:
            raise ChromeError("Chrome n'est plus connecté")
        self.seq += 1
        fid = self.seq
        fut = asyncio.get_running_loop().create_future()
        self.pending[fid] = fut
        try:
            await self._send(json.dumps({"id": fid, "cmd": cmd, **params}))
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ChromeError(f"Chrome ne répond pas ({cmd})") from None
        finally:
            self.pending.pop(fid, None)
        if not msg.get("ok"):
            raise ChromeError(msg.get("error") or f"échec de {cmd} dans Chrome")
        return msg.get("result")

    def feed(self, msg: dict) -> None:
        fut = self.pending.get(msg.get("id"))
        if fut and not fut.done():
            fut.set_result(msg)
        elif msg.get("type") == "hello":
            self.info = msg

    def close(self) -> None:
        self.closed = True
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ChromeError("Chrome s'est déconnecté"))


bridges: dict[int, ChromeBridge] = {}
browsers: dict[int, "ChromeUserBrowser"] = {}


def preference(user_id: int) -> str:
    u = auth.get_user(user_id)
    return (u or {}).get("settings", {}).get("browser", "chrome")


def chrome_for(user_id: int) -> "ChromeUserBrowser | None":
    """Le Chrome de l'utilisateur s'il est connecté et qu'il ne l'a pas écarté dans ses réglages."""
    bridge = bridges.get(user_id)
    if not bridge or bridge.closed or preference(user_id) == "interne":
        return None
    ub = browsers.get(user_id)
    if not ub or ub.bridge is not bridge:
        ub = browsers[user_id] = ChromeUserBrowser(bridge)
    return ub


@router.websocket("/api/chrome/ws")
async def chrome_ws(ws: WebSocket):
    user = auth.user_from_token(ws.query_params.get("token"))
    if not user:
        await ws.close(code=4001)
        return
    await ws.accept()
    bridge = ChromeBridge(user["id"], ws.send_text)
    old = bridges.get(user["id"])
    bridges[user["id"]] = bridge
    if old:
        old.close()  # la connexion la plus récente l'emporte (Chrome redémarré…)
    await ws.send_text(json.dumps({"type": "welcome", "user": user["name"]}))
    log.info("Chrome connecté pour %s", user["email"])
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "ping":
                await ws.send_text('{"type":"pong"}')
            else:
                bridge.feed(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bridge.close()
        if bridges.get(user["id"]) is bridge:
            bridges.pop(user["id"], None)
            browsers.pop(user["id"], None)


@router.get("/api/chrome")
def chrome_status(user=Depends(auth.current_user)):
    b = bridges.get(user["id"])
    return {"connected": bool(b and not b.closed), "version": (b.info.get("version") if b else ""),
            "use": preference(user["id"]), "folder": str(EXTENSION_DIR)}


@router.get("/api/chrome/extension.zip")
def extension_zip(url: str = "", user=Depends(auth.current_user)):
    """L'extension à installer, déjà réglée sur l'adresse d'Ely d'où on la télécharge."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(EXTENSION_DIR.rglob("*")):
            rel = p.relative_to(EXTENSION_DIR)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts) and rel.name != "config.json":
                z.write(p, f"ely-chrome/{rel}")
        url = url.strip().rstrip("/")
        if re.fullmatch(r"https?://[^\s/?#]+", url):
            z.writestr("ely-chrome/config.json", json.dumps({"url": url}))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="ely-chrome.zip"'})


# ---------------------------------------------------------------------- pilotage d'un onglet
_FUNC = re.compile(r"^\s*(async\s+)?(function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
_NO_ARG = object()
UPLOAD_MAX = 25_000_000  # octets : au-delà, la liaison WebSocket de l'extension serait trop sollicitée

FIND_JS = r"""(n) => {
  const q = '[data-ely-ref="' + n + '"]';
  const find = (root) => {
    const e = root.querySelector(q);
    if (e) return e;
    for (const h of root.querySelectorAll('*')) if (h.shadowRoot) { const f = find(h.shadowRoot); if (f) return f; }
    return null;
  };
  return find(document);
}"""

# nom de touche → (code, keyCode, texte)
KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, ""), "Escape": ("Escape", 27, ""), "Backspace": ("Backspace", 8, ""),
    "Delete": ("Delete", 46, ""), "ArrowDown": ("ArrowDown", 40, ""), "ArrowUp": ("ArrowUp", 38, ""),
    "ArrowLeft": ("ArrowLeft", 37, ""), "ArrowRight": ("ArrowRight", 39, ""), "Home": ("Home", 36, ""),
    "End": ("End", 35, ""), "PageDown": ("PageDown", 34, ""), "PageUp": ("PageUp", 33, ""), "Space": ("Space", 32, " "),
    " ": ("Space", 32, " "),
}
MODIFIERS = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}


class _Keyboard:
    def __init__(self, page: "ChromePage") -> None:
        self.page = page

    async def press(self, combo: str) -> None:
        *mods, key = combo.split("+") if combo != "+" else ["+"]
        flags = 0
        for m in mods:
            m = ("Meta" if self.page.bridge.mac else "Control") if m == "ControlOrMeta" else m
            flags |= MODIFIERS.get(m, 0)
        code, kc, text = KEYS.get(key, ("", 0, ""))
        if not code and len(key) == 1:
            code = "Key" + key.upper() if key.isalpha() else "Digit" + key if key.isdigit() else ""
            kc, text = ord(key.upper()), key
        if flags & (MODIFIERS["Control"] | MODIFIERS["Meta"] | MODIFIERS["Alt"]):
            text = ""  # raccourci : pas de caractère tapé
        base = {"key": key, "code": code, "windowsVirtualKeyCode": kc, "nativeVirtualKeyCode": kc, "modifiers": flags}
        await self.page.cdp("Input.dispatchKeyEvent", type="keyDown" if text else "rawKeyDown", text=text, **base)
        await self.page.cdp("Input.dispatchKeyEvent", type="keyUp", **base)

    async def type(self, text: str, delay: float = 0) -> None:
        await self.page.cdp("Input.insertText", text=text)


class _Mouse:
    def __init__(self, page: "ChromePage") -> None:
        self.page = page

    async def click(self, x: float, y: float) -> None:
        await self.page.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for t in ("mousePressed", "mouseReleased"):
            await self.page.cdp("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)

    async def wheel(self, dx: float, dy: float) -> None:
        vp = self.page.viewport_size
        await self.page.cdp("Input.dispatchMouseEvent", type="mouseWheel", x=vp["width"] / 2, y=vp["height"] / 2,
                            deltaX=dx, deltaY=dy)


class ChromeLocator:
    def __init__(self, page: "ChromePage", ref: str) -> None:
        self.page, self.ref = page, ref
        self.first = self

    async def _on_element(self, body: str):
        """Exécute `body` avec `e` = l'élément numéroté (lève une erreur s'il a disparu)."""
        return await self.page.evaluate(
            f"(n) => {{ const e = ({FIND_JS})(n); if (!e) throw new Error('élément [' + n + '] introuvable : refais un snapshot'); {body} }}",
            self.ref)

    async def scroll_into_view_if_needed(self, timeout: float = 0) -> None:
        await self._on_element("e.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});")

    async def click(self, timeout: float = 0) -> None:
        box = await self._on_element(
            "e.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); const r = e.getBoundingClientRect();"
            "return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height};")
        if not box["w"] or not box["h"]:
            raise ChromeError("élément invisible")
        await self.page.mouse.click(box["x"], box["y"])

    async def evaluate(self, fn: str):
        return await self._on_element(f"return ({fn})(e);")

    async def fill(self, text: str, timeout: float = 0) -> None:
        # vide le champ (setter natif, compris par React & co), puis frappe réelle du texte
        await self._on_element(r"""
          e.scrollIntoView({block: 'center', behavior: 'instant'}); e.focus();
          if (e.isContentEditable) {
            const r = document.createRange(); r.selectNodeContents(e); const s = getSelection(); s.removeAllRanges(); s.addRange(r);
          } else if ('value' in e) {
            const proto = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const set = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (set) set.call(e, ''); else e.value = '';
            e.dispatchEvent(new Event('input', {bubbles: true}));
          }""")
        if text:
            await self.page.cdp("Input.insertText", text=text)
        else:
            await self.page.keyboard.press("Backspace")

    async def press(self, key: str) -> None:
        await self._on_element("e.focus();")
        await self.page.keyboard.press(key)

    async def select_option(self, label: str | None = None, value: str | None = None, timeout: float = 0) -> None:
        await self._on_element(f"""
          const want = {json.dumps(label if label is not None else value)}, byLabel = {json.dumps(label is not None)};
          const o = [...e.options].find(o => byLabel ? o.text.trim() === want.trim() : o.value === want);
          if (!o) throw new Error('option introuvable : ' + want);
          e.value = o.value; e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}}));""")

    async def set_input_files(self, path: str) -> None:
        file = Path(path)
        size = file.stat().st_size
        r = await self.page.cdp("Runtime.evaluate", expression=f"({FIND_JS})({json.dumps(self.ref)})")
        oid = r.get("result", {}).get("objectId")
        if not oid:
            raise ChromeError("champ de fichier introuvable")
        try:
            await self.page.cdp("DOM.setFileInputFiles", files=[str(file)], objectId=oid)
            got = await self._on_element("return e.files && e.files.length ? [e.files[0].name, e.files[0].size] : null;")
            if got == [file.name, size]:
                return
        except ChromeError:
            pass
        # Chrome refuse le chemin (extension sans accès aux fichiers, ou Chrome sur une autre machine) :
        # le fichier est transmis par la liaison et déposé dans le champ comme par un glisser-déposer
        if size > UPLOAD_MAX:
            raise ChromeError(f"fichier trop lourd pour l'envoi par l'extension ({size // 1_000_000} Mo, maximum {UPLOAD_MAX // 1_000_000})")
        data = base64.b64encode(await asyncio.to_thread(file.read_bytes)).decode()
        kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        await self._on_element(f"""
          const bin = atob({json.dumps(data)}), bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          const dt = new DataTransfer();
          dt.items.add(new File([bytes], {json.dumps(file.name)}, {{type: {json.dumps(kind)}}}));
          e.files = dt.files;
          e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}}));""")


class ChromePage:
    """Un onglet de la fenêtre d'Ely dans Chrome, avec l'interface Playwright attendue par l'outil."""

    def __init__(self, bridge: ChromeBridge, tab_id: int, url: str = "") -> None:
        self.bridge, self.tab_id, self.url = bridge, tab_id, url
        self.viewport_size = {"width": 1280, "height": 860}
        self.keyboard, self.mouse = _Keyboard(self), _Mouse(self)
        self._closed = False

    async def cdp(self, method: str, timeout: float = 30, **params):
        return await self.bridge.call("cdp", timeout=timeout, tab_id=self.tab_id, method=method, params=params)

    def is_closed(self) -> bool:
        return self._closed

    def on(self, *_args) -> None:  # les dialogues sont acceptés par l'extension
        pass

    def locator(self, selector: str) -> ChromeLocator:
        m = re.search(r'data-ely-ref="(\d+)"', selector)
        if not m:
            raise ChromeError(f"sélecteur non pris en charge : {selector}")
        return ChromeLocator(self, m.group(1))

    async def evaluate(self, js: str, arg=_NO_ARG):
        expr = f"({js})({'' if arg is _NO_ARG else json.dumps(arg)})" if _FUNC.match(js) else js
        r = await self.cdp("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True, userGesture=True)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise ChromeError((d.get("exception") or {}).get("description") or d.get("text") or "erreur JavaScript")
        return (r.get("result") or {}).get("value")

    async def wait_for_load_state(self, state: str = "load", timeout: float = 30000) -> None:
        ok = ("interactive", "complete") if state == "domcontentloaded" else ("complete",)
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            try:
                info = await self.evaluate("({s: document.readyState, u: location.href})")
                self.url = info["u"]
                if info["s"] in ok:
                    if state == "networkidle":
                        await asyncio.sleep(0.5)
                    return
            except ChromeError:
                pass  # page en cours de remplacement
            await asyncio.sleep(0.15)

    async def goto(self, url: str, wait_until: str = "load", timeout: float = 30000) -> None:
        r = await self.cdp("Page.navigate", url=url, timeout=timeout / 1000)
        if r.get("errorText"):
            raise ChromeError(r["errorText"])
        self.url = url
        await asyncio.sleep(0.2)
        await self.wait_for_load_state(wait_until, timeout)

    async def go_back(self, **_kw) -> None:
        await self.evaluate("history.back()")
        await asyncio.sleep(0.4)
        await self.wait_for_load_state("domcontentloaded", 15000)

    async def title(self) -> str:
        try:
            return await self.evaluate("document.title") or ""
        except ChromeError:
            return ""

    async def bring_to_front(self) -> None:
        await self.bridge.call("activate", tab_id=self.tab_id)

    async def close(self) -> None:
        self._closed = True
        try:
            await self.bridge.call("close", tab_id=self.tab_id)
        except ChromeError:
            pass

    async def screenshot(self, type: str = "jpeg", quality: int = 55) -> bytes:
        try:
            vp = await self.evaluate("({width: innerWidth, height: innerHeight})")
            if vp and vp.get("width"):
                self.viewport_size = vp
        except ChromeError:
            pass
        r = await self.cdp("Page.captureScreenshot", timeout=8, format=type, quality=quality)
        return base64.b64decode(r["data"])


class ChromeUserBrowser(BaseUserBrowser):
    """Les onglets d'Ely dans le Chrome de l'utilisateur."""

    kind = "chrome"

    def __init__(self, bridge: ChromeBridge) -> None:
        super().__init__(bridge.user_id)
        self.bridge = bridge
        self.known: set[int] = set()
        self._no_frames_until = 0.0

    async def _tabs(self) -> list[dict]:
        return await self.bridge.call("tabs") or []

    async def page(self, key: str = "main") -> ChromePage:
        self.last_used = time.time()
        self.active_key = key
        tabs = await self._tabs()
        ids = {t["tab_id"] for t in tabs}
        p = self.pages.get(key)
        if p is not None and p.tab_id in ids and not p.is_closed():
            # un clic a ouvert un nouvel onglet depuis la page courante : il devient la page active
            new = [t for t in tabs if t["tab_id"] not in self.known and t.get("opener") == p.tab_id]
            if new:
                p = ChromePage(self.bridge, new[-1]["tab_id"], new[-1].get("url", ""))
        else:
            used = {x.tab_id for k, x in self.pages.items() if k != key}
            free = [t for t in tabs if t["tab_id"] not in used]
            if free and key == "main":
                p = ChromePage(self.bridge, free[0]["tab_id"], free[0].get("url", ""))
            else:
                p = await self.new_page(key)
        self.known |= ids | {p.tab_id}
        self.pages[key] = p
        return p

    async def new_page(self, key: str = "main") -> ChromePage:
        r = await self.bridge.call("new_tab")
        p = ChromePage(self.bridge, r["tab_id"])
        self.known.add(p.tab_id)
        self.pages[key] = p
        return p

    async def list_pages(self) -> list[ChromePage]:
        mine = {p.tab_id: p for p in self.pages.values()}
        return [mine.get(t["tab_id"]) or ChromePage(self.bridge, t["tab_id"], t.get("url", "")) for t in await self._tabs()]

    async def frame(self, page, quality: int = 55) -> str:
        # fenêtre masquée par une autre : Chrome peut refuser la capture ; on n'insiste pas à chaque action
        if time.time() < self._no_frames_until:
            raise ChromeError("capture indisponible pour l'instant")
        try:
            return base64.b64encode(await page.screenshot(type="jpeg", quality=quality)).decode()
        except ChromeError:
            self._no_frames_until = time.time() + 60
            raise

    async def close(self) -> None:
        pass  # la fenêtre appartient à l'utilisateur
