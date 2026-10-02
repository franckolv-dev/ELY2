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
