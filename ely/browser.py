"""Navigateur piloté par l'agent (Playwright/Chromium).

Chaque utilisateur a un profil persistant : cookies et connexions restent d'une
tâche à l'autre (on se connecte une fois à Doctolib, LinkedIn…). L'agent lit la
page sous forme d'une liste compacte d'éléments numérotés et agit par numéro.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path

from .config import settings

log = logging.getLogger("ely.browser")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1280, "height": 860}
IDLE_CLOSE_S = 15 * 60

SNAPSHOT_JS = r"""
(maxItems) => {
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option], [role=combobox], [role=switch], [role=textbox], [contenteditable=""], [contenteditable=true], summary, [onclick]';
  const vh = innerHeight, vw = innerWidth;
  document.querySelectorAll('[data-ely-ref]').forEach(e => e.removeAttribute('data-ely-ref'));
  const all = [];
  const collect = (root) => {
    root.querySelectorAll(SEL).forEach(e => all.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) collect(e.shadowRoot); });
  };
  collect(document);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const nameOf = (el) => {
    let t = el.getAttribute('aria-label') || '';
    if (!t && el.labels && el.labels.length) t = el.labels[0].innerText;
    if (!t && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && !['INPUT','SELECT','TEXTAREA'].includes(el.tagName)) t = el.innerText;
    if (!t) t = el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || '';
    if (!t) { const img = el.querySelector && el.querySelector('img[alt]'); if (img) t = img.alt; }
    return clean(t).slice(0, 90);
  };
  const cands = [];
  const seen = new Set();
  all.forEach((el, idx) => {
    if (seen.has(el)) return; seen.add(el);
    if (el.closest('[aria-hidden=true]')) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) < 0.05) return;
    const inView = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    const dist = inView ? 0 : (r.top >= vh ? r.top - vh : -r.bottom);
    cands.push({el, idx, r, inView, dist});
  });
  const chosen = [...cands].sort((a, b) => a.dist - b.dist).slice(0, maxItems).sort((a, b) => a.idx - b.idx);
  const lines = [];
  let n = 0;
  for (const c of chosen) {
    const el = c.el; n++;
    el.setAttribute('data-ely-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    let kind = role || tag;
    if (tag === 'a') kind = 'lien';
    if (tag === 'button' || role === 'button') kind = 'bouton';
    if (tag === 'input') kind = 'champ(' + (el.type || 'text') + ')';
    if (tag === 'textarea' || role === 'textbox' || el.isContentEditable) kind = 'zone-texte';
    if (tag === 'select') kind = 'liste';
    let line = '[' + n + '] ' + kind + ' "' + nameOf(el) + '"';
    if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) line += el.checked ? ' ☑' : ' ☐';
    else if (tag === 'input' || tag === 'textarea') { if (el.value) line += ' valeur="' + clean(el.value).slice(0, 60) + '"'; }
    if (tag === 'select') {
      const opts = [...el.options].slice(0, 12).map(o => (o.selected ? '*' : '') + clean(o.text).slice(0, 30));
      line += ' options=[' + opts.join(' | ') + (el.options.length > 12 ? ' | …' : '') + ']';
    }
    if (tag === 'a') { const h = el.getAttribute('href') || ''; if (h && !h.startsWith('javascript')) line += ' → ' + h.slice(0, 80); }
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') line += ' (désactivé)';
    if (el.required) line += ' (obligatoire)';
    if (!c.inView) line += c.r.top >= vh ? ' ↓' : ' ↑';
    lines.push(line);
  }
  // texte visible autour de l'écran courant
  const parts = [];
  let total = 0;
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode()) && total < 3500) {
    const t = clean(node.textContent);
    if (t.length < 2) continue;
    const p = node.parentElement;
    if (!p || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName)) continue;
    const r = p.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < -vh * 0.3 || r.top > vh * 1.6) continue;
    if (parts.length && parts[parts.length - 1] === t) continue;
    parts.push(t); total += t.length + 1;
  }
  const sh = document.documentElement.scrollHeight;
  return {
    title: document.title, url: location.href, elements: lines, total: cands.length,
    text: parts.join('\n'), scroll: Math.round(scrollY), height: sh, vh
  };
}
"""

CONSENT_JS = r"""
() => {
  const rx = /^(tout accepter|accepter tout|accepter et fermer|accepter les cookies|j'accepte|accepter|autoriser tous les cookies|tout autoriser|accept all|accept all cookies|allow all|i agree|agree)$/i;
  const box = /(cookie|consent|didomi|onetrust|axeptio|cmp|gdpr|rgpd|tarteaucitron|qc-cmp|sp_message|cookiebot)/i;
  const btns = [...document.querySelectorAll('button, [role=button], a')];
  for (const b of btns) {
    const t = (b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!rx.test(t)) continue;
    let p = b, ok = false;
    for (let i = 0; i < 8 && p; i++, p = p.parentElement) { if (box.test((p.id || '') + ' ' + (p.className || ''))) { ok = true; break; } }
    if (ok) { b.click(); return t; }
  }
  return '';
}
"""


class BaseUserBrowser:
    """Ce qui est commun au navigateur interne et au Chrome de l'utilisateur."""

    kind = "interne"

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.pages: dict[str, object] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.last_used = time.time()
        self.active_key = "main"
        self.on_frame = None  # rappel pour la vue en direct

    def lock(self, key: str) -> asyncio.Lock:
        return self.locks.setdefault(key, asyncio.Lock())

    async def settle(self, page, timeout: float = 4000) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
            await page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass

    async def snapshot(self, page, max_items: int = 150) -> str:
        await self.settle(page, 2500)
        for _ in range(2):
            try:
                s = await page.evaluate(SNAPSHOT_JS, max_items)
                break
            except Exception as e:  # navigation en cours
                await asyncio.sleep(1)
                err = e
        else:
            return f"(page illisible : {err})"
        pages_count = max(1, round(s["height"] / max(1, s["vh"])))
        cur = min(pages_count, 1 + round(s["scroll"] / max(1, s["vh"])))
        tabs = await self.list_pages()
        head = f"Page : {s['title']}\nURL : {s['url']}\nÉcran {cur}/{pages_count}"
        if len(tabs) > 1:
            head += f" · {len(tabs)} onglets ouverts"
        more = f"\n(… {s['total'] - len(s['elements'])} autres éléments hors écran)" if s["total"] > len(s["elements"]) else ""
        return (f"{head}\n\n## Texte visible\n{s['text'][:3500]}\n\n## Éléments interactifs (utilise ref=N)\n"
                + "\n".join(s["elements"]) + more)

    async def frame(self, page, quality: int = 55) -> str:
        data = await page.screenshot(type="jpeg", quality=quality)
        return base64.b64encode(data).decode()


class UserBrowser(BaseUserBrowser):
    """Navigateur interne (Chromium piloté par Playwright), avec un profil persistant par utilisateur."""

    def __init__(self, user_id: int, context) -> None:
        super().__init__(user_id)
        self.context = context
        context.on("page", self._on_new_page)

    def _on_new_page(self, page) -> None:
        # un clic a ouvert un nouvel onglet : il devient la page active
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        self.pages[self.active_key] = page

    async def page(self, key: str = "main"):
        self.last_used = time.time()
        self.active_key = key
        p = self.pages.get(key)
        if p is None or p.is_closed():
            open_pages = [x for x in self.context.pages if not x.is_closed() and x not in self.pages.values()]
            p = open_pages[0] if (open_pages and key == "main") else await self.context.new_page()
            p.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            self.pages[key] = p
        return p

    async def new_page(self, key: str = "main"):
        p = await self.context.new_page()
        p.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        self.pages[key] = p
        return p

    async def list_pages(self) -> list:
        return [p for p in self.context.pages if not p.is_closed()]

    async def close(self) -> None:
        try:
            await self.context.close()
        except Exception:
            pass


class BrowserManager:
    def __init__(self) -> None:
        self._pw = None
        self._shared = None
        self.users: dict[int, UserBrowser] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    def _launch_kwargs(self) -> dict:
        kw: dict = {"headless": settings.browser_headless,
                    "args": ["--disable-blink-features=AutomationControlled", "--no-default-browser-check", "--no-first-run"]}
        if settings.browser_executable:
            kw["executable_path"] = settings.browser_executable
        elif settings.browser_channel:
            kw["channel"] = settings.browser_channel
        return kw

    async def _playwright(self):
        if self._pw is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            if self._reaper is None:
                self._reaper = asyncio.create_task(self._reap())
        return self._pw

    def current(self, user_id: int) -> BaseUserBrowser | None:
        """Le navigateur qu'Ely utilise en ce moment pour cet utilisateur (Chrome ou interne)."""
        from .chrome import chrome_for

        return chrome_for(user_id) or self.users.get(user_id)

    async def for_user(self, user_id: int) -> BaseUserBrowser:
        """Chrome de l'utilisateur si l'extension est connectée, sinon navigateur interne."""
        from .chrome import chrome_for

        chrome = chrome_for(user_id)
        if chrome:
            return chrome
        async with self._lock:
            ub = self.users.get(user_id)
            if ub:
                ub.last_used = time.time()
                return ub
            pw = await self._playwright()
            profile = settings.user_dir(user_id) / "navigateur"
            profile.mkdir(parents=True, exist_ok=True)
            ctx = await pw.chromium.launch_persistent_context(
                str(profile), **self._launch_kwargs(), viewport=VIEWPORT, user_agent=UA, locale="fr-FR",
                timezone_id=settings.timezone, accept_downloads=True)
            await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            downloads = settings.user_dir(user_id) / "files" / "Téléchargements"

            async def on_download(dl):
                downloads.mkdir(parents=True, exist_ok=True)
                await dl.save_as(str(downloads / dl.suggested_filename))

            ctx.on("page", lambda p: p.on("download", lambda d: asyncio.ensure_future(on_download(d))))
            for p in ctx.pages:
                p.on("download", lambda d: asyncio.ensure_future(on_download(d)))
            ub = UserBrowser(user_id, ctx)
            self.users[user_id] = ub
            return ub

    async def shared(self):
        async with self._lock:
            if self._shared is None or not self._shared.is_connected():
                pw = await self._playwright()
                kw = self._launch_kwargs()
                kw["headless"] = True
                self._shared = await pw.chromium.launch(**kw)
            return self._shared

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(60)
            for uid, ub in list(self.users.items()):
                if time.time() - ub.last_used > IDLE_CLOSE_S and not any(l.locked() for l in ub.locks.values()):
                    self.users.pop(uid, None)
                    await ub.close()

    async def shutdown(self) -> None:
        for ub in list(self.users.values()):
            await ub.close()
        self.users.clear()
        if self._shared:
            try:
                await self._shared.close()
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()
            self._pw = None


manager = BrowserManager()


async def render_page(url: str, wait_ms: int = 1500) -> tuple[str, str]:
    """Rendu ponctuel d'une page JavaScript (sans cookies), pour la lecture de contenu."""
    browser = await manager.shared()
    ctx = await browser.new_context(user_agent=UA, locale="fr-FR", viewport=VIEWPORT)
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            await page.wait_for_timeout(wait_ms)
        return await page.title(), await page.content()
    finally:
        await ctx.close()


def safe_upload_path(user_id: int, path: str) -> Path:
    root = (settings.user_dir(user_id) / "files").resolve()
    p = (root / path).resolve() if not Path(path).is_absolute() else Path(path)
    return p
