"""Lecture de pages : un site qui bloque les robots oriente vers le navigateur au lieu d'une erreur brute."""
from __future__ import annotations

import http.server
import socket
import threading

import pytest
from conftest import new_conversation

from ely.tools import ToolContext, execute


async def _noop(*a, **k):
    pass


@pytest.fixture
def site():
    """Un marchand protégé : la fiche produit répond 403 aux lectures automatiques, le reste 404 ou 200."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status = {"/fiche": 403, "/page": 200}.get(self.path, 404)
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            body = b"<p>Contenu lisible de la page.</p>" * 20 if status == 200 else b"<p>Access denied</p>"
            self.wfile.write(b"<html><title>Page</title><body>" + body + b"</body></html>")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://localhost:{port}"
    server.shutdown()


async def test_web_fetch_names_the_bot_block_and_points_to_the_browser(site, user):
    """Darty, Fnac… répondent 403 à web_fetch : le modèle doit comprendre qu'insister est vain et ouvrir la page avec
    browser, plutôt que de lire une exception HTTP avec un lien vers la documentation Mozilla."""
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    r = await execute(ctx, "web_fetch", {"url": f"{site}/fiche"})
    assert r.is_error
    assert "HTTP 403" in r.content and "browser action=open" in r.content and "inutile de réessayer" in r.content
    assert "HTTPStatusError" not in r.content and "mozilla" not in r.content.lower()

    r = await execute(ctx, "web_fetch", {"url": f"{site}/absente"})
    assert r.is_error and "HTTP 404" in r.content and "HTTPStatusError" not in r.content

    r = await execute(ctx, "web_fetch", {"url": f"{site}/page"})
    assert not r.is_error and "Contenu lisible" in r.content
