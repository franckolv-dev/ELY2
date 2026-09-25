"""Client MCP : Ely se branche sur des serveurs MCP externes, dont les outils deviennent les siens.

Configuration (administrateur) : data/mcp.json
  {"servers": {
     "maison": {"url": "http://homeassistant.local:8123/mcp"},
     "fichiers": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/franck/Documents"]}
  }}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from contextlib import AsyncExitStack

from .config import settings
from .tools import TOOLS, Tool, ToolContext, ToolResult, tool

log = logging.getLogger("ely.mcp")
CONFIG = settings.data_dir / "mcp.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text()).get("servers", {}) if CONFIG.exists() else {}
    except Exception as e:
        log.warning("mcp.json illisible : %s", e)
        return {}


def save_config(servers: dict) -> None:
    CONFIG.write_text(json.dumps({"servers": servers}, indent=2, ensure_ascii=False))


def _tool_name(server: str, name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server}_{name}")[:64]


class Connection:
    def __init__(self, name: str, spec: dict) -> None:
        self.name = name
        self.spec = spec
        self.session = None
        self.tools: list[str] = []
        self.status = "démarrage"
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def _open(self, stack: AsyncExitStack):
        from mcp import ClientSession

        if self.spec.get("url"):
            import mcp.client.streamable_http as sh

            factory = getattr(sh, "streamable_http_client", None) or getattr(sh, "streamablehttp_client")
            streams = await stack.enter_async_context(factory(self.spec["url"]))
        else:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(command=self.spec["command"], args=self.spec.get("args", []),
                                           env={**os.environ, **self.spec.get("env", {})})
            streams = await stack.enter_async_context(stdio_client(params))
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def run(self) -> None:
        delay = 5
        while not self.stop.is_set():
            try:
                async with AsyncExitStack() as stack:
                    self.session = await self._open(stack)
                    listed = await self.session.list_tools()
                    self._register(listed.tools)
                    self.status = f"ok ({len(self.tools)} outils)"
                    self.ready.set()
                    delay = 5
                    await self.stop.wait()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.status = f"erreur : {e.__class__.__name__}: {str(e)[:200]}"
                log.warning("MCP %s : %s", self.name, self.status)
                self.ready.set()
            finally:
                self._unregister()
                self.session = None
            if not self.stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)

    def _register(self, tools) -> None:
        self._unregister()
        for t in tools:
            tname = _tool_name(self.name, t.name)
            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {"type": "object", "properties": {}}
            schema = schema if isinstance(schema, dict) else schema.model_dump()
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})

            async def call(ctx: ToolContext, _remote=t.name, **args) -> ToolResult:
                return await self.call(_remote, args)

            TOOLS[tname] = Tool(name=tname, description=f"[{self.name}] {(t.description or t.name)[:900]}", parameters=schema,
                                func=call, label=f"{self.name} · {t.name}", icon="🧩", timeout=300, source=f"mcp:{self.name}")
            self.tools.append(tname)

    def _unregister(self) -> None:
        for tname in self.tools:
            TOOLS.pop(tname, None)
        self.tools = []

    async def call(self, remote: str, args: dict) -> ToolResult:
        if not self.session:
            return ToolResult(f"Serveur MCP {self.name} déconnecté ({self.status}).", is_error=True)
        res = await self.session.call_tool(remote, args)
        texts, images = [], []
        for c in res.content or []:
            kind = getattr(c, "type", "")
            if kind == "text":
                texts.append(c.text)
            elif kind == "image":
                images.append({"media_type": getattr(c, "mimeType", None) or getattr(c, "mime_type", "image/png"), "data": c.data})
            elif kind == "resource":
                r = c.resource
                texts.append(getattr(r, "text", None) or f"[ressource {getattr(r, 'uri', '')}]")
        structured = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
        if structured and not texts:
            texts.append(json.dumps(structured, ensure_ascii=False, indent=1))
        is_error = bool(getattr(res, "isError", None) or getattr(res, "is_error", False))
        return ToolResult("\n".join(texts) or "(aucune sortie)", images=images, is_error=is_error)


class Manager:
    def __init__(self) -> None:
        self.conns: dict[str, Connection] = {}

    async def start_all(self, wait: float = 10) -> None:
        await self.stop_all()
        for name, spec in load_config().items():
            if spec.get("disabled"):
                continue
            c = Connection(name, spec)
            c.task = asyncio.create_task(c.run())
            self.conns[name] = c
        if self.conns:
            await asyncio.wait([asyncio.create_task(c.ready.wait()) for c in self.conns.values()], timeout=wait)

    async def stop_all(self) -> None:
        for c in self.conns.values():
            c.stop.set()
            if c.task:
                c.task.cancel()
        await asyncio.gather(*(c.task for c in self.conns.values() if c.task), return_exceptions=True)
        for c in self.conns.values():
            c._unregister()
        self.conns.clear()

    def status(self) -> dict:
        return {n: {"status": c.status, "tools": c.tools} for n, c in self.conns.items()}


manager = Manager()


@tool("mcp_servers", """Gère les serveurs MCP branchés sur Ely (leurs outils deviennent les tiens, préfixés mcp_).
action=list · add(name, command+args OU url, env?) · remove(name). Exemples : serveur de fichiers, Home Assistant, Notion, GitHub…""",
      {"action": {"type": "string", "enum": ["list", "add", "remove"]}, "name": {"type": "string"},
       "command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}, "url": {"type": "string"},
       "env": {"type": "object"}},
      ["action"], label="Extensions MCP", icon="🧩", admin_only=True, timeout=120)
async def mcp_servers(ctx: ToolContext, action: str, name: str = "", command: str = "", args: list[str] | None = None,
                      url: str = "", env: dict | None = None) -> ToolResult:
    servers = load_config()
    if action == "list":
        st = manager.status()
        lines = [f"- {n} : {st.get(n, {}).get('status', 'arrêté')} — {', '.join(st.get(n, {}).get('tools', [])[:15])}" for n in servers]
        return ToolResult("\n".join(lines) or "Aucun serveur MCP.")
    if not re.fullmatch(r"[a-z0-9_-]{2,30}", name or ""):
        return ToolResult("name invalide (minuscules, chiffres, - ou _)", is_error=True)
    if action == "remove":
        servers.pop(name, None)
    else:
        if not (command or url):
            return ToolResult("command ou url requis", is_error=True)
        servers[name] = {"url": url} if url else {"command": command, "args": args or [], "env": env or {}}
    save_config(servers)
    await manager.start_all()
    return ToolResult(f"Serveurs MCP : {json.dumps(manager.status(), ensure_ascii=False)}")
