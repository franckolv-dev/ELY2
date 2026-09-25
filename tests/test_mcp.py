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
