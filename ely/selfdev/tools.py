"""Outils d'auto-amélioration.

`self_improve` (administrateur) lance une session d'amélioration. Dans cette session,
Ely dispose d'outils pour mesurer ses performances, lire et modifier son propre code,
écrire des plugins, ajuster ses leçons, tester et se redéployer.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..db import db, now
from ..tools import TOOLS, ToolContext, ToolResult, execute, tool
from . import metrics, pipeline, plugins


def _selfdev(ctx: ToolContext) -> bool:
    return bool(ctx.extra.get("selfdev")) and ctx.is_admin


SELFDEV_GUIDE = """# Mode auto-amélioration
Tu travailles sur TON PROPRE fonctionnement (Ely) pour devenir plus efficace, rapide, fiable et économe.
Méthode :
1. Diagnostique : ely_metrics (échecs, erreurs d'outils, lenteurs, refus du contrôleur, insatisfactions, coûts),
   puis examine les cas concrets : ely_journal (list pour trouver la tâche, read pour son déroulé complet), recall, code concerné.
2. Choisis le levier le plus simple et le plus sûr qui règle la cause :
   a) une leçon générale de comportement → ely_guidelines (effet immédiat, pour tous)
   b) une procédure qui marche → skill_save avec shared=true
   c) un outil manquant ou plus fiable → ely_plugin (Python, chargé à chaud, sans redémarrage)
   d) un défaut ou une lenteur du cœur → ely_code / ely_edit dans la copie de travail, ely_test, puis ely_deploy.
3. Cœur : changements petits et ciblés, un test ajouté pour chaque correction, ely_test vert avant ely_deploy.
   Ne supprime ni n'affaiblis jamais un test pour le faire passer. ely_deploy publie le commit testé sur une branche GitHub
   et ouvre une PR attribuée à Ely (auto-amélioration), puis active localement le code et redémarre (retour arrière si échec).
   La publication doit réussir avant l'activation. Donne le lien de la PR et demande à l'administrateur de la fusionner ;
   laisse-lui la fusion GitHub. Les leçons, souvenirs et données privées restent hors du dépôt.
4. Tu peux améliorer ton processus d'amélioration lui-même (ely/selfdev/, ce guide compris).
5. Termine par un compte rendu : problèmes trouvés, améliorations appliquées, effet attendu, idées pour la suite.
Architecture : ely/agent (boucle, prompts, runner) · ely/llm (modèles) · ely/tools (outils) · ely/memory · ely/selfdev ·
ely/api (HTTP) · ely/web (interface) · tests/ (pytest, modèle simulé)."""


# ---------------------------------------------------------------------- mission confiée à Claude (Agent SDK)
CLAUDE_GUIDE = """# Auto-amélioration d'Ely
Ely (agent personnel autonome : FastAPI + SQLite + PWA) te confie l'amélioration de son propre code et de son
fonctionnement. Le répertoire courant est une copie de travail isolée de son code (branche ely-self) : rien n'y est actif
avant ely_deploy. Respecte les règles de CLAUDE.md.
Outils :
- Read, Glob, Grep, Edit, Write : la copie de travail uniquement (.env, data, .git, .venv et .claude sont refusés) ;
- mcp__ely__ely_metrics : performances récentes (échecs, erreurs d'outils, lenteurs, refus du contrôleur, coûts) ;
  mcp__ely__ely_journal : tâches passées (list, filtrable par texte) et déroulé complet d'une tâche (read : demandes,
  actions avec arguments et résultats, erreurs, refus du contrôleur) : le point de départ pour comprendre un échec ;
  mcp__ely__recall : souvenirs et conversations passées ; mcp__ely__ely_code : diff et reset de la copie ;
- mcp__ely__ely_test : suite de tests sur la copie (pattern = filtre -k) ; tu n'as pas de terminal ;
- mcp__ely__ely_deploy : tests, commit, push sur une branche GitHub dédiée et PR attribuée à Ely (auto-amélioration),
  puis fusion locale dans la version active ; si la publication échoue, aucune activation. Donne le lien de la PR
  et demande à l'administrateur de la fusionner sur GitHub ; laisse-lui cette fusion. Ely redémarre à la fin de ta
  mission, avec retour arrière automatique si elle ne démarre pas ;
- mcp__ely__ely_guidelines, mcp__ely__skill_save (shared=true), mcp__ely__ely_plugin : leçons, compétences, plugins à chaud.
Méthode : diagnostique d'abord (ely_metrics, puis ely_journal sur les cas concrets), puis choisis le levier le plus simple et le plus sûr qui règle
la cause (leçon, compétence, plugin, code). Pour le code : changements petits et ciblés, un test de comportement pour
chaque correction, ely_test vert avant ely_deploy ; ne supprime ni n'affaiblis jamais un test.
Termine par un compte rendu en français, en vouvoyant l'administrateur : problèmes trouvés, améliorations appliquées
(déployées ou non), effet attendu, idées pour la suite."""
CLAUDE_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write"]
CLAUDE_BRIDGE = ["ely_metrics", "ely_journal", "recall", "ely_code", "ely_test", "ely_deploy", "ely_guidelines", "skill_save", "ely_plugin"]


def claude_guard(name: str, args: dict) -> str | None:
    """Outils natifs de Claude : la copie de travail seulement, hors zones protégées. Raison du refus, ou None."""
    targets = [args.get("file_path") or args.get("path") or "."]
    if name == "Glob":
        targets.append(args.get("pattern") or "")
    if name == "Grep" and args.get("glob"):
        targets.append(args["glob"])
    for target in filter(None, targets):
        try:
            if str(target).startswith("~"):
                raise ValueError("chemin hors du code d'Ely")
            rel = pipeline.resolve(str(target)).relative_to(pipeline.WORKTREE.resolve())
            if rel.parts and rel.parts[0] == ".claude":  # réglages et crochets de Claude Code : hors de sa portée
                raise ValueError("chemin protégé : .claude")
        except ValueError as e:
            return (f"{name} refusé ({target}) : {e}. Seule la copie de travail {pipeline.WORKTREE} est accessible, "
                    "hors .env, data, .git, .venv et .claude.")
    return None


def claude_session(ctx: ToolContext, objective: str, model: str):
    """Mission d'auto-amélioration pour Claude : outils natifs gardés, outils d'Ely par MCP, budget des réglages."""
    from ..llm import claude_agent, registry

    async def call(name: str, args: dict) -> ToolResult:
        return await execute(ctx, name, args)

    return claude_agent.Session(prompt=objective, cwd=Path(pipeline.WORKTREE), model=model, system=CLAUDE_GUIDE,
                                tools=list(CLAUDE_TOOLS), bridge=[TOOLS[n] for n in CLAUDE_BRIDGE if n in TOOLS],
                                call=call, permit=claude_guard, budget_usd=claude_agent.budget(),
                                effort=registry.effort("selfdev"))


def claude_report(user: dict, objective: str, model: str, error: str, actions: list[dict], texts: list[str],
                  diff: str, deployed: bool) -> str:
    """Consigne dans un fichier markdown (Fichiers de l'administrateur) ce que Claude a fait avant d'être arrêté.
    Renvoie son chemin dans l'espace de fichiers."""
    import time

    from ..config import settings

    rel = f"auto-amelioration/{time.strftime('%Y-%m-%d-%H%M%S')}-mission-claude.md"
    steps = "\n".join(f"{i}. {a['icon']} {a['label']}" + (f" ({a['args']})" if a.get("args") else "")
                      + {True: " : ok", False: " : ÉCHEC"}.get(a.get("ok"), " : sans résultat")
                      + (f"\n   > {a['preview'][:300]}".replace("\n", " ") if a.get("preview") else "")
                      for i, a in enumerate(actions, 1)) or "(aucune action)"
    said = "\n\n".join(t.strip() for t in texts if t.strip())[-6000:] or "(rien)"
    body = f"""# Mission d'auto-amélioration interrompue : quota de Claude atteint

- **Date** : {time.strftime('%d/%m/%Y %H:%M')}
- **Modèle** : {model}
- **Arrêt** : {error}
- **Déploiement** : {"oui, une partie du travail est déjà active" if deployed else "aucun"}

## Mission
{objective}

## Ce que Claude a fait
{steps}

## Ce que Claude a expliqué
{said}

## Modifications non déployées dans la copie de travail
```diff
{diff.strip()[:40000] or "(aucune)"}
```
"""
    path = settings.user_dir(user["id"]) / "files" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return rel


def relaunch_objective(objective: str, report: str, actions: list[dict], diff: str, deployed: bool) -> str:
    """Nouvelle mission pour le modèle d'escalade : la même, plus ce que Claude a déjà fait."""
    done = "\n".join(f"- {a['label']}" + (f" ({a['args']})" if a.get("args") else "")
                      + {True: " : ok", False: " : échec"}.get(a.get("ok"), " : sans résultat") for a in actions[-40:])
    stat = diff.split("\n\n", 1)[0].strip() or "aucune"
    return (f"Mission relancée : Claude a commencé cette mission mais son quota a été atteint avant la fin.\n\n"
            f"Mission d'origine :\n{objective}\n\n"
            f"Ce que Claude a déjà fait (détail complet dans le fichier {report}, lisible avec file_read) :\n{done}\n\n"
            f"Modifications non déployées dans la copie de travail :\n{stat}\n"
            + ("Une partie du travail a déjà été déployée.\n" if deployed else "")
            + "\nRecommence la mission en tenant compte de ce travail : vérifie ce qui est en place (ely_code action=diff), "
              "garde ce qui est bon, corrige ou termine le reste. L'objectif restant peut donc différer un peu de la "
              "mission d'origine.")


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
      admin_only=True, available=_selfdev, effects=False)
async def ely_metrics(ctx: ToolContext, days: float = 7) -> ToolResult:
    return ToolResult(metrics.report(days))


@tool("ely_journal", """Journal des tâches passées d'Ely, pour comprendre un échec précis. action=list(query?, days?) : tâches
récentes, filtrables par mots (titre, demande, déroulé) · read(run_id, offset?) : déroulé complet d'une tâche (demandes, textes,
actions avec arguments et résultats, erreurs, refus du contrôleur).""",
      {"action": {"type": "string", "enum": ["list", "read"]}, "query": {"type": "string"},
       "days": {"type": "number", "description": "Période en jours (défaut 30)"}, "run_id": {"type": "integer"},
       "offset": {"type": "integer"}},
      ["action"], label="Journal des tâches", icon="📜", admin_only=True, available=_selfdev, timeout=60,
      effects=False)
async def ely_journal(ctx: ToolContext, action: str, query: str = "", days: float = 30, run_id: int = 0, offset: int = 0) -> ToolResult:
    if action == "read":
        if not run_id:
            return ToolResult("run_id manquant : action=list pour trouver la tâche.", is_error=True)
        return ToolResult(metrics.journal_read(run_id, offset))
    return ToolResult(metrics.journal_list(query, days))


@tool("ely_code", """Lit le code source d'Ely dans la copie de travail. action=list(path?) · read(path, offset?) · search(pattern regex, path?)
· diff (modifications en cours) · reset (repartir de la version active).""",
      {"action": {"type": "string", "enum": ["list", "read", "search", "diff", "reset"]}, "path": {"type": "string"},
       "pattern": {"type": "string"}, "offset": {"type": "integer"}},
      ["action"], label="Code d'Ely", icon="🧬", admin_only=True, available=_selfdev, timeout=180, effects=False)
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
      {"pattern": {"type": "string"}}, [], label="Tests", icon="🧪", admin_only=True, available=_selfdev, timeout=960, effects=False)
async def ely_test(ctx: ToolContext, pattern: str = "") -> ToolResult:
    await pipeline.ensure_session()
    ok, out = await pipeline.run_tests(pattern)
    return ToolResult(("✅ Tests OK\n" if ok else "❌ Tests en échec\n") + out, is_error=not ok)


@tool("ely_deploy", "Teste et commit les modifications, pousse une branche GitHub et ouvre une PR à faire fusionner par l'administrateur, puis active localement et redémarre Ely.",
      {"summary": {"type": "string", "description": "Résumé de l'amélioration (message de commit)"}},
      ["summary"], label="Déploiement", icon="🚀", admin_only=True, available=_selfdev, timeout=1200)
async def ely_deploy(ctx: ToolContext, summary: str) -> ToolResult:
    res = await pipeline.deploy(summary, restart=not ctx.extra.get("defer_restart"))
    if res["ok"]:
        ctx.extra["deployed"] = True
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
            await plugins.check_import(path)
            tools = plugins.load_one(name)
            result = await plugins.test_one(name)
        except (Exception, SystemExit) as e:
            if old is not None:
                path.write_text(old)
                plugins.load_one(name)
            else:
                plugins._unload(name)
                path.unlink(missing_ok=True)
            return ToolResult(f"Plugin refusé (ancienne version conservée) : {e.__class__.__name__}: {e}", is_error=True)
        plugins.set_disabled(name, False)
        db.insert("improvements", kind="plugin", title=f"Plugin {name} {'mis à jour' if old else 'créé'} : {', '.join(tools)}",
                  diff=code[:20000], status="applied", created_at=now())
        return ToolResult(f"Plugin {name} actif : outils {', '.join(tools)} — {result}. Ils sont disponibles dès la prochaine tâche.")
    if action == "test":
        try:
            return ToolResult(await plugins.test_one(name))
        except (Exception, SystemExit) as e:
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
