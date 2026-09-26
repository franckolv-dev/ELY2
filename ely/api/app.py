"""Application web d'Ely : API + interface (PWA) servies par le même processus."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import CODE_VERSION, __version__
from ..agent.runner import runner
from .. import chrome
from ..browser import manager
from ..llm import registry
from ..tools import TOOLS, load_builtin_tools
from . import admin, chat, settings_routes

log = logging.getLogger("ely")
WEB = Path(__file__).resolve().parent.parent / "web"


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
    await runner.shutdown()
    await manager.shutdown()
    await mcp.stop_all()


def create_app() -> FastAPI:
    app = FastAPI(title="Ely", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(chat.router)
    app.include_router(settings_routes.router)
    app.include_router(admin.router)
    app.include_router(chrome.router)

    @app.get("/api/health")
    async def health():
        from ..db import db

        db.val("SELECT 1")
        ok = len(TOOLS) >= 15
        return JSONResponse({"ok": ok, "version": __version__, "code": CODE_VERSION, "tools": len(TOOLS),
                             "providers": registry.status}, status_code=200 if ok else 503)

    @app.get("/api/tools")
    def tools():
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
        return page("index.html")

    return app


app = create_app()
