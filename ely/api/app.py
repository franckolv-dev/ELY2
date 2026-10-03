"""Application web d'Ely : API + interface (PWA) servies par le même processus."""
from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from .. import CODE_VERSION, __version__, auth
from ..agent.runner import runner
from .. import chrome
from ..browser import manager
from ..llm import registry
from ..tools import TOOLS, load_builtin_tools
from . import admin, chat, settings_routes

log = logging.getLogger("ely")
WEB = Path(__file__).resolve().parent.parent / "web"


class HideTokens(logging.Filter):
    """Les jetons transmis dans l'adresse (session de l'extension Chrome, flux iCal) ne s'écrivent jamais dans les
    journaux : celui de l'extension donne accès à tout le compte."""
    SECRET = re.compile(r"((?:[?&]token=)|(?:/ics/))[^&\s\"'/]+?(?=\.ics\b|[&\s\"']|$)")

    def filter(self, record: logging.LogRecord) -> bool:
        hide = lambda v: self.SECRET.sub(r"\1•••", v) if isinstance(v, str) else v
        record.msg = hide(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(hide(a) for a in record.args)
        return True


def hide_tokens_in_logs() -> None:
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, HideTokens) for f in logger.filters):
            logger.addFilter(HideTokens())


# Défense en profondeur pour l'interface : seuls ses propres scripts s'exécutent, aucune image ni connexion vers un
# autre site (une réponse manipulée ne peut pas faire fuiter de données par l'adresse d'une image), pas d'affichage
# dans le cadre d'un autre site.
APP_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "media-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
           "frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"x-content-type-options": "nosniff", "referrer-policy": "same-origin", "x-frame-options": "DENY"}


class SecurityHeaders:
    """En-têtes de sécurité sur toutes les réponses ; ceux qu'une route fixe elle-même (fichiers servis) priment.
    La politique de contenu (APP_CSP) est posée par la page de l'interface elle-même."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    if k not in headers:
                        headers[k] = v
                if scope.get("scheme") == "https":
                    headers.setdefault("strict-transport-security", "max-age=31536000")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class Static(StaticFiles):
    """Interface revalidée à chaque chargement (réponse 304 si rien n'a changé) : après une mise à jour,
    le navigateur ne ressert jamais l'ancienne version depuis son cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..channels.telegram import polling_loop
    from ..scheduler import scheduler_loop
    from ..mcp_client import manager as mcp
    from ..selfdev.pipeline import check_rollback

    from ..memory.seed import seed_skills

    load_builtin_tools()
    check_rollback()
    seed_skills()
    await asyncio.gather(registry.refresh(), mcp.start_all())
    resumed = await runner.resume_all()
    log.info("Ely %s (%s) prête : %d outils, fournisseurs %s, %d tâche(s) reprise(s)", __version__, CODE_VERSION, len(TOOLS),
             registry.status, resumed)
    background = [asyncio.create_task(scheduler_loop()), asyncio.create_task(polling_loop())]
    yield
    for t in background:
        t.cancel()
    for step in (runner.shutdown(), manager.shutdown(), mcp.stop_all()):  # une étape bloquée n'empêche pas les suivantes
        try:
            await asyncio.wait_for(step, 5)
        except Exception as e:
            log.warning("arrêt incomplet : %r", e)


def create_app() -> FastAPI:
    hide_tokens_in_logs()
    # pas de carte de l'API en libre accès sur Internet
    app = FastAPI(title="Ely", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeaders)
    app.include_router(chat.router)
    app.include_router(settings_routes.router)
    app.include_router(admin.router)
    app.include_router(chrome.router)

    @app.get("/api/health")
    async def health(request: Request):
        from ..db import db

        db.val("SELECT 1")
        ok = len(TOOLS) >= 15
        body = {"ok": ok, "code": CODE_VERSION, "tools": len(TOOLS)}
        user = auth.user_from_token(auth.token_from_request(request))
        if user and user["role"] == "admin":  # l'état des fournisseurs (et leurs erreurs) ne regarde que l'administrateur
            body.update(version=__version__, providers=registry.status)
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/tools")
    def tools(user=Depends(auth.current_user)):
        return [{"name": t.name, "label": t.label, "icon": t.icon, "source": t.source} for t in TOOLS.values()]

    app.mount("/static", Static(directory=WEB), name="static")

    def page(name: str, media: str | None = None):
        return FileResponse(WEB / name, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def sw():
        return page("sw.js", "application/javascript")

    @app.get("/favicon.ico")
    def favicon():  # demandé d'office par certains navigateurs et lecteurs de flux
        return FileResponse(WEB / "icons" / "favicon-32.png", media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return page("manifest.webmanifest", "application/manifest+json")

    @app.get("/")
    @app.get("/share")
    def index():
        response = page("index.html")
        response.headers["Content-Security-Policy"] = APP_CSP
        return response

    return app


app = create_app()
