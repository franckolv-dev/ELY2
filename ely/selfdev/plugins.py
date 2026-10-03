"""Plugins : outils écrits par Ely elle-même, chargés à chaud sans redémarrage.

Un plugin est un fichier Python de data/plugins/ qui déclare un ou plusieurs outils
avec le décorateur @tool. Un plugin qui ne se charge pas est désactivé automatiquement.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from ..config import ROOT, settings
from ..db import db, now

log = logging.getLogger("ely.plugins")

PLUGIN_DIR = settings.data_dir / "plugins"
IMPORT_LIMIT = 30  # secondes pour se charger dans le processus d'essai
CHECK = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("ely_plugin_essai", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("PLUGIN-OK")"""
LOADED: dict[str, list[str]] = {}  # plugin -> noms d'outils
SHADOWED: dict[str, dict] = {}  # plugin -> outils de base qu'il remplace (restaurés au déchargement)

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
    TOOLS.update(SHADOWED.pop(name, {}))
    sys.modules.pop(f"ely_plugin_{name}", None)


def load_one(name: str) -> list[str]:
    """(Re)charge un plugin ; renvoie les outils enregistrés. Lève en cas d'erreur."""
    from ..tools import TOOLS

    path = PLUGIN_DIR / f"{name}.py"
    _unload(name)
    before = dict(TOOLS)
    spec = importlib.util.spec_from_file_location(f"ely_plugin_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException as e:  # sys.exit() à l'import compris : Ely ne doit jamais s'arrêter pour un plugin
        for t in [t for t in TOOLS if TOOLS[t] is not before.get(t)]:
            TOOLS.pop(t, None)
        TOOLS.update({t: v for t, v in before.items() if t not in TOOLS})
        sys.modules.pop(spec.name, None)
        if isinstance(e, Exception):
            raise
        raise RuntimeError(f"le plugin a voulu arrêter Ely au chargement ({e.__class__.__name__})") from e
    new = sorted(t for t in TOOLS if TOOLS[t] is not before.get(t))
    for t in new:
        TOOLS[t].source = f"plugin:{name}"
    LOADED[name] = new
    SHADOWED[name] = {t: before[t] for t in new if t in before}
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


async def check_import(path: Path) -> None:
    """Charge le plugin dans un processus à part avant de l'accepter : s'il quitte, plante ou ne finit pas de se
    charger, il est refusé (installé, il empêcherait Ely de redémarrer)."""
    tmp = tempfile.mkdtemp(prefix="ely-plugin-")
    env = {**os.environ, "ELY_DATA_DIR": tmp, "PYTHONPATH": str(ROOT)}
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", CHECK, str(path), cwd=str(ROOT), env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), IMPORT_LIMIT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"le plugin ne finit pas de se charger (plus de {IMPORT_LIMIT} s)") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = out.decode(errors="replace").strip()
    if proc.returncode or "PLUGIN-OK" not in text:
        raise RuntimeError(f"le plugin ne se charge pas sans arrêter Python : {text[-800:] or f'code {proc.returncode}'}")


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
