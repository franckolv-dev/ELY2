"""Client MCP : un vrai serveur MCP (stdio) branché sur Ely, ses outils deviennent ceux d'Ely."""
from __future__ import annotations

import sys

import pytest
from conftest import new_conversation

from ely import mcp_client
from ely.tools import TOOLS, ToolContext, execute

SERVER = '''
try:
    from mcp.server import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

app = Server("demo")

@app.tool()
def meteo_maison(piece: str) -> str:
    """Température d'une pièce de la maison."""
    return f"{piece} : 21,5 °C"

if __name__ == "__main__":
    app.run()
'''


async def _noop(*a, **k):
    pass


async def test_mcp_stdio_server_tools_become_ely_tools(user, tmp_path, monkeypatch):
    script = tmp_path / "srv.py"
    script.write_text(SERVER)
    monkeypatch.setattr(mcp_client, "CONFIG", tmp_path / "mcp.json")
    mcp_client.save_config({"maison": {"command": sys.executable, "args": [str(script)]}})
    await mcp_client.manager.start_all(wait=30)
    try:
        st = mcp_client.manager.status()["maison"]
        assert st["status"].startswith("ok"), st
        assert "mcp_maison_meteo_maison" in TOOLS
        ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
        r = await execute(ctx, "mcp_maison_meteo_maison", {"piece": "salon"})
        assert "salon : 21,5 °C" in r.content, r.content
    finally:
        await mcp_client.manager.stop_all()
    assert "mcp_maison_meteo_maison" not in TOOLS


MCP_SERVER = """import os, sys
from mcp.server.mcpserver import MCPServer

open(sys.argv[1], "a").write(f"{os.getpid()}\\n")
server = MCPServer("maison")


@server.tool()
def lumiere(piece: str) -> str:
    \"\"\"Allume la lumière d'une pièce.\"\"\"
    return f"lumière allumée : {piece}"


server.run()
"""


async def test_a_dead_mcp_server_is_reconnected(user, tmp_path, monkeypatch):
    """Le serveur MCP de la maison s'arrête (mise à jour, plantage) : Ely le détecte et s'y reconnecte, au lieu de
    répondre « Connection closed » jusqu'à son propre redémarrage."""
    import asyncio
    import os
    import signal

    monkeypatch.setattr(mcp_client, "PING_EVERY", 0.5)
    script, pids = tmp_path / "maison.py", tmp_path / "pids"
    script.write_text(MCP_SERVER)
    monkeypatch.setattr(mcp_client, "CONFIG", tmp_path / "mcp.json")
    mcp_client.save_config({"maison": {"command": sys.executable, "args": [str(script), str(pids)]}})
    ctx = ToolContext(user=user, conversation_id=0, run_id=0, emit=_noop)
    try:
        await mcp_client.manager.start_all(wait=30)
        r = await execute(ctx, "mcp_maison_lumiere", {"piece": "salon"})
        assert not r.is_error and "lumière allumée : salon" in r.content, r.content
        os.kill(int(pids.read_text().split()[0]), signal.SIGKILL)
        for _ in range(200):
            await asyncio.sleep(0.1)
            if len(pids.read_text().split()) == 2 and "mcp_maison_lumiere" in TOOLS:
                break
        r = await execute(ctx, "mcp_maison_lumiere", {"piece": "cuisine"})
        assert not r.is_error and "lumière allumée : cuisine" in r.content, r.content
    finally:
        await mcp_client.manager.stop_all()


SECRETS_SERVER = """import os
try:
    from mcp.server import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

app = Server("maison")


@app.tool()
def cles() -> str:
    "Variables reçues par le serveur."
    return f"openai={os.environ.get('OPENAI_API_KEY', '')} github={os.environ.get('ELY_GITHUB_TOKEN', '')} " \\
           f"maison={os.environ.get('MAISON_TOKEN', '')}"


if __name__ == "__main__":
    app.run()
"""


async def test_an_mcp_server_gets_no_secret_and_serves_the_admin_unless_shared(user, tmp_path, monkeypatch):
    """Un serveur MCP est un programme tiers : il ne reçoit ni les clés d'Ely ni ses jetons, seulement ce que sa
    configuration lui donne. Branché par l'administrateur, ses outils lui sont réservés, sauf « shared »: true."""
    from ely import auth
    from ely.db import db

    monkeypatch.setenv("OPENAI_API_KEY", "sk-cle-d-ely")
    monkeypatch.setenv("ELY_GITHUB_TOKEN", "ghp-jeton-d-ely")
    script = tmp_path / "maison.py"
    script.write_text(SECRETS_SERVER)
    monkeypatch.setattr(mcp_client, "CONFIG", tmp_path / "mcp.json")
    lea = auth.create_user(f"lea{tmp_path.name}@x.fr", "Léa", "motdepasse")
    db.run("UPDATE users SET role = 'user' WHERE id = ?", (lea["id"],))
    lea = auth.get_user(lea["id"])
    spec = {"command": sys.executable, "args": [str(script)], "env": {"MAISON_TOKEN": "jeton-de-la-maison"}}
    for shared in (False, True):
        mcp_client.save_config({"maison": {**spec, **({"shared": True} if shared else {})}})
        await mcp_client.manager.start_all(wait=30)
        try:
            franck = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
            r = await execute(franck, "mcp_maison_cles", {})
            assert "openai= github= maison=jeton-de-la-maison" in r.content, r.content
            r = await execute(ToolContext(user=lea, conversation_id=new_conversation(lea), run_id=0, emit=_noop),
                              "mcp_maison_cles", {})
            assert r.is_error is not shared, (shared, r.content)
        finally:
            await mcp_client.manager.stop_all()


async def test_a_server_whose_command_is_gone_says_which_one(tmp_path, monkeypatch):
    """Après un redémarrage du Mac, la commande d'un serveur MCP n'existe plus : son état le dit en la nommant (sous
    uvloop, l'erreur du lancement ne nomme aucun fichier), sans empêcher les autres serveurs de démarrer."""
    monkeypatch.setattr(mcp_client, "CONFIG", tmp_path / "mcp.json")
    mcp_client.save_config({"ely_code": {"command": "/Users/franck/outils/serveur-disparu", "args": []}})
    await mcp_client.manager.start_all(wait=10)
    try:
        status = mcp_client.manager.status()["ely_code"]["status"]
        assert "commande introuvable" in status and "/Users/franck/outils/serveur-disparu" in status, status
    finally:
        await mcp_client.manager.stop_all()


async def test_a_tool_name_is_never_added_as_an_mcp_server(user, tmp_path, monkeypatch):
    """Ely prend son propre outil « ely_code » pour un serveur MCP et l'ajoute : refusé en nommant la commande, rien
    n'est enregistré (l'erreur revenait à chaque démarrage). Un vrai programme, trouvé par le PATH de sa configuration,
    s'ajoute comme avant."""
    import asyncio
    from pathlib import Path

    monkeypatch.setattr(mcp_client, "CONFIG", tmp_path / "mcp.json")
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    try:
        r = await execute(ctx, "mcp_servers", {"action": "add", "name": "ely_code", "command": "ely_code"})
        assert r.is_error and "« ely_code »" in r.content, r.content
        assert mcp_client.load_config() == {}
        script = tmp_path / "srv.py"
        script.write_text(SERVER)
        python = Path(sys.executable)
        r = await execute(ctx, "mcp_servers", {"action": "add", "name": "maison", "command": python.name,
                                               "args": [str(script)], "env": {"PATH": str(python.parent)}})
        assert not r.is_error and "maison" in mcp_client.load_config(), r.content
        await asyncio.wait_for(mcp_client.manager.conns["maison"].ready.wait(), 30)
        assert mcp_client.manager.status()["maison"]["status"].startswith("ok")
    finally:
        await mcp_client.manager.stop_all()
