"""Plugins : outils écrits par Ely elle-même, chargés à chaud sans redémarrage.

Un plugin est un fichier Python de data/plugins/ qui déclare un ou plusieurs outils
avec le décorateur @tool. Un plugin qui ne se charge pas est désactivé automatiquement.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import traceback
from pathlib import Path

from ..config import settings
from ..db import db, now

log = logging.getLogger("ely.plugins")

PLUGIN_DIR = settings.data_dir / "plugins"
LOADED: dict[str, list[str]] = {}  # plugin -> noms d'outils

TEMPLATE = '''"""Plugin Ely : {description}"""
from ely.tools import ToolContext, ToolResult, tool


@tool("{name}", "{description}", {{"query": {{"type": "string"}}}}, ["query"], label="{name}", icon="🔧")
async def {name}(ctx: ToolContext, query: str) -> ToolResult:
    return ToolResult(f"résultat pour {{query}}")


async def selftest():
    """Facultatif : lève une exception si le plugin ne fonctionne pas."""
'''


def disabled() -> set[str]:
    return set(db.get_setting("plugins_disabled", []))


def set_disabled(name: str, off: bool) -> None:
    d = disabled()
    (d.add if off else d.discard)(name)
    db.set_setting("plugins_disabled", sorted(d))


def _unload(name: str) -> None:
    from ..tools import TOOLS

    for tname in LOADED.pop(name, []):
        TOOLS.pop(tname, None)
    sys.modules.pop(f"ely_plugin_{name}", None)


def load_one(name: str) -> list[str]:
    """(Re)charge un plugin ; renvoie les outils enregistrés. Lève en cas d'erreur."""
    from ..tools import TOOLS

    path = PLUGIN_DIR / f"{name}.py"
    _unload(name)
    before = set(TOOLS)
    spec = importlib.util.spec_from_file_location(f"ely_plugin_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        for t in set(TOOLS) - before:
            TOOLS.pop(t, None)
        sys.modules.pop(spec.name, None)
        raise
    new = sorted(set(TOOLS) - before)
    for t in new:
        TOOLS[t].source = f"plugin:{name}"
    LOADED[name] = new
    return new


def load_plugins() -> dict[str, str]:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    status = {}
    off = disabled()
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        name = path.stem
        if name in off:
            status[name] = "désactivé"
            continue
        try:
            tools = load_one(name)
            status[name] = f"ok ({', '.join(tools)})"
        except Exception as e:
            set_disabled(name, True)
            status[name] = f"erreur, désactivé : {e}"
            db.insert("improvements", kind="plugin", title=f"Plugin {name} désactivé automatiquement",
                      detail=traceback.format_exc()[-2000:], status="disabled", created_at=now())
            log.warning("plugin %s désactivé : %s", name, e)
    return status


async def test_one(name: str) -> str:
    module = sys.modules.get(f"ely_plugin_{name}")
    if not module:
        return "plugin non chargé"
    fn = getattr(module, "selftest", None)
    if not fn:
        return "chargé (pas de selftest)"
    res = fn()
    if asyncio.iscoroutine(res):
        res = await asyncio.wait_for(res, 120)
    return f"selftest ok {res if res is not None else ''}".strip()


def list_plugins() -> list[dict]:
    PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    off = disabled()
    return [{"name": p.stem, "enabled": p.stem not in off, "tools": LOADED.get(p.stem, []),
             "size": p.stat().st_size} for p in sorted(PLUGIN_DIR.glob("*.py"))]


def plugin_path(name: str) -> Path:
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", name):
        raise ValueError("nom de plugin invalide (minuscules, chiffres, _)")
    return PLUGIN_DIR / f"{name}.py"
