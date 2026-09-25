"""Outils d'auto-amélioration.

`self_improve` (administrateur) lance une session d'amélioration. Dans cette session,
Ely dispose d'outils pour mesurer ses performances, lire et modifier son propre code,
écrire des plugins, ajuster ses leçons, tester et se redéployer.
"""
from __future__ import annotations

import re
import subprocess

from ..db import db, now
from ..tools import ToolContext, ToolResult, tool
from . import metrics, pipeline, plugins


def _selfdev(ctx: ToolContext) -> bool:
    return bool(ctx.extra.get("selfdev")) and ctx.is_admin


SELFDEV_GUIDE = """# Mode auto-amélioration
Tu travailles sur TON PROPRE fonctionnement (Ely) pour devenir plus efficace, rapide, fiable et économe.
Méthode :
1. Diagnostique : ely_metrics (échecs, erreurs d'outils, lenteurs, refus du contrôleur, insatisfactions, coûts),
   puis examine les cas concrets (recall, lecture du code concerné).
2. Choisis le levier le plus simple et le plus sûr qui règle la cause :
   a) une leçon générale de comportement → ely_guidelines (effet immédiat, pour tous)
   b) une procédure qui marche → skill_save avec shared=true
   c) un outil manquant ou plus fiable → ely_plugin (Python, chargé à chaud, sans redémarrage)
   d) un défaut ou une lenteur du cœur → ely_code / ely_edit dans la copie de travail, ely_test, puis ely_deploy.
3. Cœur : changements petits et ciblés, un test ajouté pour chaque correction, ely_test vert avant ely_deploy.
   Ne supprime ni n'affaiblis jamais un test pour le faire passer. ely_deploy redémarre Ely (retour arrière automatique si échec).
4. Tu peux améliorer ton processus d'amélioration lui-même (ely/selfdev/, ce guide compris).
5. Termine par un compte rendu : problèmes trouvés, améliorations appliquées, effet attendu, idées pour la suite.
Architecture : ely/agent (boucle, prompts, runner) · ely/llm (modèles) · ely/tools (outils) · ely/memory · ely/selfdev ·
ely/api (HTTP) · ely/web (interface) · tests/ (pytest, modèle simulé)."""


@tool("self_improve", """Lance une session d'auto-amélioration d'Ely en arrière-plan (analyse des performances, nouvelles
compétences, plugins, corrections de son propre code avec tests et redéploiement). Précise l'objectif (ex. « sois plus rapide
pour les RDV Doctolib ») ou laisse vide pour une amélioration générale.""",
      {"goal": {"type": "string"}}, [], label="Auto-amélioration", icon="🛠️", admin_only=True, subagent=False,
      available=lambda ctx: not ctx.extra.get("selfdev"))
async def self_improve(ctx: ToolContext, goal: str = "") -> ToolResult:
    cid = start_session(ctx.user, goal)
    return ToolResult(f"Session d'auto-amélioration lancée (conversation #{cid} « 🛠️ Auto-amélioration »). "
                      "Le compte rendu y apparaîtra.")


def start_session(user: dict, goal: str = "") -> int:
    import asyncio

    from ..agent.runner import runner

    conv = db.one("SELECT id FROM conversations WHERE user_id = ? AND channel = 'selfdev' ORDER BY id DESC LIMIT 1", (user["id"],))
    cid = conv["id"] if conv else db.insert("conversations", user_id=user["id"], title="🛠️ Auto-amélioration",
                                             channel="selfdev", created_at=now(), updated_at=now())
    objective = goal.strip() or ("Analyse tes performances récentes et améliore-toi là où c'est le plus utile "
                                 "(fiabilité d'abord, puis vitesse et coût).")
    asyncio.get_running_loop().create_task(runner.submit(user, cid, objective, channel="selfdev"))
    return cid


@tool("ely_metrics", "Rapport de performances d'Ely (tâches, statuts, durées, erreurs d'outils, refus du contrôleur, insatisfactions, coûts).",
      {"days": {"type": "number", "description": "Période en jours (défaut 7)"}}, [], label="Métriques", icon="📊",
      admin_only=True, available=_selfdev)
async def ely_metrics(ctx: ToolContext, days: float = 7) -> ToolResult:
    return ToolResult(metrics.report(days))


@tool("ely_code", """Lit le code source d'Ely dans la copie de travail. action=list(path?) · read(path, offset?) · search(pattern regex, path?)
· diff (modifications en cours) · reset (repartir de la version active).""",
      {"action": {"type": "string", "enum": ["list", "read", "search", "diff", "reset"]}, "path": {"type": "string"},
       "pattern": {"type": "string"}, "offset": {"type": "integer"}},
      ["action"], label="Code d'Ely", icon="🧬", admin_only=True, available=_selfdev, timeout=180)
async def ely_code(ctx: ToolContext, action: str, path: str = "", pattern: str = "", offset: int = 0) -> ToolResult:
    await pipeline.ensure_session()
    if action == "reset":
        sha = await pipeline.prepare()
        return ToolResult(f"Copie de travail remise à la version active ({sha[:10]}).")
    if action == "diff":
        return ToolResult(await pipeline.diff() or "Aucune modification.")
    base = pipeline.resolve(path or ".")
    if action == "list":
        files = [str(p.relative_to(pipeline.WORKTREE)) for p in sorted(base.rglob("*"))
                 if p.is_file() and not any(x in p.parts for x in (".git", "__pycache__", "vendor", "node_modules"))]
        return ToolResult("\n".join(files[:500]))
    if action == "read":
        text = base.read_text(encoding="utf-8")
        lines = text.splitlines()
        chunk = lines[offset: offset + 400]
        more = f"\n[… lignes {offset + 400}+ : relis avec offset={offset + 400}]" if len(lines) > offset + 400 else ""
        return ToolResult("\n".join(f"{i + offset + 1:5d}  {l}" for i, l in enumerate(chunk)) + more)
    if action == "search":
        rx = re.compile(pattern)
        hits = []
        for p in sorted(base.rglob("*.py")) if base.is_dir() else [base]:
            if "vendor" in p.parts:
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p.relative_to(pipeline.WORKTREE)}:{n}: {line.strip()[:160]}")
        return ToolResult("\n".join(hits[:200]) or "Aucune occurrence.")
    return ToolResult("action inconnue", is_error=True)


@tool("ely_edit", """Modifie le code d'Ely dans la copie de travail (pas encore actif). action=replace(path, old, new : remplacement exact
d'un passage unique) · write(path, content : fichier entier) · delete(path).""",
      {"action": {"type": "string", "enum": ["replace", "write", "delete"]}, "path": {"type": "string"},
       "old": {"type": "string"}, "new": {"type": "string"}, "content": {"type": "string"}},
      ["action", "path"], label="Modification du code", icon="✏️", admin_only=True, available=_selfdev, timeout=60)
async def ely_edit(ctx: ToolContext, action: str, path: str, old: str = "", new: str = "", content: str = "") -> ToolResult:
    await pipeline.ensure_session()
    p = pipeline.resolve(path)
    if action == "delete":
        p.unlink()
        return ToolResult(f"{path} supprimé (copie de travail).")
    if action == "write":
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    else:
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            return ToolResult(f"Le passage à remplacer apparaît {n} fois (il doit être unique). Ajoute du contexte.", is_error=True)
        p.write_text(text.replace(old, new), encoding="utf-8")
    if p.suffix == ".py":
        r = subprocess.run(["python3", "-m", "py_compile", str(p)], capture_output=True, text=True)
        if r.returncode:
            return ToolResult(f"Modifié, mais ERREUR DE SYNTAXE :\n{r.stderr[-1500:]}", is_error=True)
    return ToolResult(f"{path} modifié dans la copie de travail. Lance ely_test puis ely_deploy.")


@tool("ely_test", "Lance la suite de tests d'Ely sur la copie de travail (pattern = filtre pytest -k facultatif).",
      {"pattern": {"type": "string"}}, [], label="Tests", icon="🧪", admin_only=True, available=_selfdev, timeout=960)
async def ely_test(ctx: ToolContext, pattern: str = "") -> ToolResult:
    await pipeline.ensure_session()
    ok, out = await pipeline.run_tests(pattern)
    return ToolResult(("✅ Tests OK\n" if ok else "❌ Tests en échec\n") + out, is_error=not ok)


@tool("ely_deploy", "Valide les modifications de la copie de travail (tests obligatoires), les active et redémarre Ely.",
      {"summary": {"type": "string", "description": "Résumé de l'amélioration (message de commit)"}},
      ["summary"], label="Déploiement", icon="🚀", admin_only=True, available=_selfdev, timeout=1200)
async def ely_deploy(ctx: ToolContext, summary: str) -> ToolResult:
    res = await pipeline.deploy(summary)
    return ToolResult(res["message"], is_error=not res["ok"])


@tool("ely_plugin", """Gère les plugins (outils Python écrits par Ely, actifs immédiatement sans redémarrage).
action=list · read(name) · write(name, code) · test(name) · disable(name) · enable(name).
Modèle de plugin :
```python
from ely.tools import ToolContext, ToolResult, tool

@tool("nom_outil", "Description claire", {"param": {"type": "string"}}, ["param"], label="Libellé", icon="🔧")
async def nom_outil(ctx: ToolContext, param: str) -> ToolResult:
    ...  # httpx, ctx.workspace, ctx.user_id, from ely.browser import manager…
    return ToolResult("résultat")

async def selftest():  # facultatif : lève une exception si ça ne marche pas
    ...
```""",
      {"action": {"type": "string", "enum": ["list", "read", "write", "test", "disable", "enable"]},
       "name": {"type": "string"}, "code": {"type": "string"}},
      ["action"], label="Plugins", icon="🔌", admin_only=True, available=_selfdev, timeout=180)
async def ely_plugin(ctx: ToolContext, action: str, name: str = "", code: str = "") -> ToolResult:
    if action == "list":
        items = plugins.list_plugins()
        return ToolResult("\n".join(f"- {p['name']} ({'actif' if p['enabled'] else 'désactivé'}) : {', '.join(p['tools'])}" for p in items)
                          or "Aucun plugin.")
    path = plugins.plugin_path(name)
    if action == "read":
        return ToolResult(path.read_text() if path.exists() else "plugin introuvable", is_error=not path.exists())
    if action == "write":
        old = path.read_text() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        try:
            tools = plugins.load_one(name)
            result = await plugins.test_one(name)
        except Exception as e:
            if old is not None:
                path.write_text(old)
                plugins.load_one(name)
            else:
                path.unlink(missing_ok=True)
            return ToolResult(f"Plugin refusé (ancienne version conservée) : {e.__class__.__name__}: {e}", is_error=True)
        plugins.set_disabled(name, False)
        db.insert("improvements", kind="plugin", title=f"Plugin {name} {'mis à jour' if old else 'créé'} : {', '.join(tools)}",
                  diff=code[:20000], status="applied", created_at=now())
        return ToolResult(f"Plugin {name} actif : outils {', '.join(tools)} — {result}. Ils sont disponibles dès la prochaine tâche.")
    if action == "test":
        try:
            return ToolResult(await plugins.test_one(name))
        except Exception as e:
            return ToolResult(f"selftest en échec : {e}", is_error=True)
    if action in ("disable", "enable"):
        plugins.set_disabled(name, action == "disable")
        if action == "disable":
            plugins._unload(name)
        else:
            plugins.load_one(name)
        return ToolResult(f"Plugin {name} {'désactivé' if action == 'disable' else 'activé'}.")
    return ToolResult("action inconnue", is_error=True)


@tool("ely_guidelines", """Lit ou remplace les « leçons tirées de l'expérience » : consignes générales injectées dans le contexte de
chaque tâche (effet immédiat). action=get · set(content : liste markdown complète, concise, max ~25 lignes).""",
      {"action": {"type": "string", "enum": ["get", "set"]}, "content": {"type": "string"}},
      ["action"], label="Leçons", icon="📝", admin_only=True, available=_selfdev, timeout=20)
async def ely_guidelines(ctx: ToolContext, action: str, content: str = "") -> ToolResult:
    if action == "get":
        return ToolResult(db.get_setting("learned_guidelines", "") or "(aucune leçon)")
    db.set_setting("learned_guidelines", content.strip()[:6000])
    db.insert("improvements", kind="guidelines", title="Leçons mises à jour", diff=content[:6000], status="applied", created_at=now())
    return ToolResult("Leçons mises à jour : elles s'appliquent dès la prochaine tâche.")
