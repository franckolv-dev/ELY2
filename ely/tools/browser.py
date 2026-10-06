"""Outil `browser` : l'agent navigue, clique, remplit des formulaires comme un humain."""
from __future__ import annotations

import asyncio

from ..browser import CONSENT_JS, manager
from ..chrome import FIND_JS, ChromeLost
from ..netguard import refusal
from . import ToolContext, ToolResult, tool, uncertain

ACTIONS = ["open", "snapshot", "click", "type", "select", "press", "scroll", "back", "screenshot", "wait",
           "text", "tabs", "switch_tab", "eval", "upload", "close_tab"]


ELEMENT_ACTIONS = {"click", "type", "select", "upload"}
ACTING = ELEMENT_ACTIONS | {"press", "eval", "back"}  # agissent sur la page : une coupure en plein vol rend l'issue incertaine


async def _present(page, ref: int) -> bool:
    """L'élément numéroté est-il toujours dans la page ? (il disparaît quand la page change après la lecture)"""
    try:
        return bool(await page.evaluate(f"(n) => !!({FIND_JS})(n)", str(ref)))
    except Exception:
        return True  # dans le doute, l'action elle-même dira ce qu'il en est


async def _emit_frame(ctx: ToolContext, ub, page) -> None:
    try:
        frame = await ub.frame(page)
        await ctx.emit("browser_frame", {"image": frame, "url": page.url})
    except Exception:
        pass


@tool(
    "browser",
    """Navigateur web réel. Extension connectée : le Chrome de l'utilisateur (toutes ses sessions : messagerie, Doctolib…),
dans une fenêtre dédiée ; sinon un navigateur interne à session persistante.
Pour agir sur n'importe quel site : rendez-vous, publication, formulaire, achat, connexion.
Après chaque action tu reçois l'état de la page : texte visible + éléments interactifs numérotés [N]. Agis avec ref=N.
Actions : open(url, new_tab?) · snapshot · click(ref) · type(ref, text, submit?) · select(ref, text) · press(key: Enter, Tab, Escape, ArrowDown…)
· scroll(direction: down|up) · back · screenshot (image de la page) · wait(seconds) · text (tout le texte de la page)
· tabs · switch_tab(tab) · eval(js) · upload(ref, text=chemin du fichier) · close_tab.
Identifiants : outil credentials. Code de vérification envoyé par e-mail : ouvre la messagerie web dans un nouvel onglet
(open new_tab, ex. outlook.office.com), lis le dernier message, puis switch_tab. Captcha ou code introuvable : ask_user.""",
    {
        "action": {"type": "string", "enum": ACTIONS},
        "url": {"type": "string"},
        "ref": {"type": "integer", "description": "Numéro de l'élément [N]"},
        "text": {"type": "string", "description": "Texte à saisir, option à choisir, ou chemin de fichier (upload)"},
        "submit": {"type": "boolean", "description": "Appuyer sur Entrée après la saisie"},
        "new_tab": {"type": "boolean", "description": "open : dans un nouvel onglet (la page courante reste ouverte)"},
        "key": {"type": "string"},
        "direction": {"type": "string", "enum": ["down", "up"]},
        "seconds": {"type": "number"},
        "tab": {"type": "integer", "description": "Index d'onglet (switch_tab)"},
        "js": {"type": "string", "description": "Code JavaScript (eval), renvoie une valeur"},
    },
    ["action"], label="Navigateur", icon="🌐", timeout=150, untrusted=True,
)
async def browser(ctx: ToolContext, action: str, **kw) -> ToolResult:
    url = kw.get("url") or ""
    if action == "open" and url and url != "about:blank" and (why := await refusal(url, ctx.is_admin)):
        return ToolResult(f"Adresse refusée : {why}.", is_error=True)  # avant même d'ouvrir un navigateur
    try:
        return await _browser(ctx, action, **kw)
    except ChromeLost as e:
        if action in ACTING:
            return ToolResult(uncertain(f"{e} pendant l'action {action}"), is_error=True)
        raise


async def _browser(ctx: ToolContext, action: str, url: str = "", ref: int | None = None, text: str = "",
                   submit: bool = False, key: str = "", direction: str = "down", seconds: float = 2,
                   tab: int | None = None, js: str = "", new_tab: bool = False) -> ToolResult:
    ub = await manager.for_user(ctx.user_id)
    # La page courante appartient à la conversation (elle survit à une réponse
    # utilisateur), jamais à toutes les routines d'un même compte. Les sous-agents
    # gardent leur propre page, dans l'espace de leur conversation parente.
    scope = f"conversation-{ctx.conversation_id}" if ctx.conversation_id else f"run-{ctx.run_id}"
    page_key = f"{scope}:{ctx.extra.get('browser_key', 'main')}"
    # active_key et on_frame sont partagés par le navigateur : sérialiser l'action
    # entière, pas seulement les appels utilisant une même page. Le verrou est
    # libéré entre appels d'outils, y compris sur erreur ou annulation.
    async with ub.lock("tool-actions"):
        page = await ub.page(page_key)
        ub.on_frame = ctx.emit

        def loc():
            if ref is None:
                raise ValueError("paramètre ref manquant")
            return page.locator(f'[data-ely-ref="{ref}"]').first

        # numéro périmé : la page a changé depuis la dernière lecture ; on rend la page à jour plutôt qu'une erreur
        if action in ELEMENT_ACTIONS and ref is not None and not await _present(page, ref):
            snap = await ub.snapshot(page)
            await _emit_frame(ctx, ub, page)
            return ToolResult(f"L'élément [{ref}] n'existe plus : la page a changé depuis la dernière lecture. "
                              f"Page actuelle, avec ses nouveaux numéros :\n\n{snap}", is_error=True)

        note = ""
        if action == "open":
            if not url:
                return ToolResult("url manquante", is_error=True)
            if not url.startswith(("http://", "https://", "file:", "about:")):
                url = "https://" + url
            if new_tab:
                page = await ub.new_page(page_key)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                return ToolResult(f"Impossible d'ouvrir {url} : {e}", is_error=True)
            await ub.settle(page)
            try:
                clicked = await page.evaluate(CONSENT_JS)
                if clicked:
                    note = f"(bandeau cookies fermé : « {clicked} »)\n"
                    await asyncio.sleep(0.6)
            except Exception:
                pass
        elif action == "click":
            el = loc()
            try:
                await el.scroll_into_view_if_needed(timeout=5000)
                await el.click(timeout=8000)
            except ChromeLost:
                raise  # le clic est peut-être parti : surtout pas un second par JavaScript
            except Exception:
                await el.evaluate("e => e.click()")
            await ub.settle(page)
            page = await ub.page(page_key)  # un nouvel onglet a pu s'ouvrir
        elif action == "type":
            el = loc()
            try:
                await el.fill(text, timeout=8000)
            except Exception:
                await el.click(timeout=8000)
                await page.keyboard.press("ControlOrMeta+A")
                await page.keyboard.type(text, delay=15)
            if submit:
                await el.press("Enter")
                await ub.settle(page)
            else:
                await asyncio.sleep(0.4)  # laisse apparaître les suggestions d'autocomplétion
        elif action == "select":
            el = loc()
            try:
                await el.select_option(label=text, timeout=5000)
            except Exception:
                await el.select_option(value=text, timeout=5000)
            await ub.settle(page, 2000)
        elif action == "press":
            await page.keyboard.press(key or "Enter")
            await ub.settle(page, 3000)
        elif action == "scroll":
            await page.mouse.wheel(0, 700 if direction == "down" else -700)
            await asyncio.sleep(0.5)
        elif action == "back":
            await page.go_back(wait_until="domcontentloaded")
            await ub.settle(page)
        elif action == "wait":
            await asyncio.sleep(max(0.2, min(float(seconds), 30)))
        elif action == "screenshot":
            img = await ub.frame(page, quality=70)
            await ctx.emit("browser_frame", {"image": img, "url": page.url})
            return ToolResult(f"Capture de {page.url}", images=[{"media_type": "image/jpeg", "data": img}])
        elif action == "text":
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            return ToolResult(f"URL : {page.url}\n\n{body}")
        elif action == "tabs":
            pages = await ub.list_pages()
            lines = [f"{i}: {await p.title()} — {p.url}{' (actif)' if p is page else ''}" for i, p in enumerate(pages)]
            if ub.kind == "chrome":
                lines.append("(onglets de la fenêtre d'Ely dans Chrome)")
            return ToolResult("\n".join(lines) or "aucun onglet")
        elif action == "switch_tab":
            pages = await ub.list_pages()
            if tab is None or not (0 <= tab < len(pages)):
                return ToolResult("index d'onglet invalide (voir action=tabs)", is_error=True)
            page = pages[tab]
            ub.pages[page_key] = page
            await page.bring_to_front()
        elif action == "close_tab":
            await page.close()
            page = await ub.page(page_key)
        elif action == "eval":
            code = js.strip()
            if code.startswith(("(", "function", "async")):
                expr = code  # fonction complète
            elif "return" in code or ";" in code or "\n" in code:
                expr = f"() => {{ {code} }}"  # corps d'instructions
            else:
                expr = code  # simple expression, ex. document.title
            val = await page.evaluate(expr)
            return ToolResult(f"Résultat : {val!r}"[:20000])
        elif action == "upload":
            path = ctx.resolve_path(text)
            if not path.exists():
                return ToolResult(f"Fichier introuvable : {text}", is_error=True)
            await loc().set_input_files(str(path))
        elif action != "snapshot":
            return ToolResult(f"action inconnue : {action}", is_error=True)

        snap = await ub.snapshot(page)
        await _emit_frame(ctx, ub, page)
        return ToolResult(note + snap)
