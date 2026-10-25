"""Auto-amélioration : plugins à chaud, métriques, modification du code avec tests et déploiement."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import new_conversation

from ely.db import db
from ely.selfdev import pipeline, plugins
from ely.tools import TOOLS, ToolContext, execute, tools_for


async def _noop(*a, **k):
    pass


@pytest.fixture
def dev_ctx(user):
    return ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop, extra={"selfdev": True})


def test_dev_tools_only_in_selfdev_sessions(user):
    normal = {t.name for t in tools_for(ToolContext(user=user, conversation_id=0, run_id=0, emit=_noop))}
    dev = {t.name for t in tools_for(ToolContext(user=user, conversation_id=0, run_id=0, emit=_noop, extra={"selfdev": True}))}
    assert "self_improve" in normal and "ely_edit" not in normal
    assert {"ely_edit", "ely_test", "ely_deploy", "ely_plugin", "ely_metrics", "ely_journal"} <= dev and "ely_journal" not in normal
    lambda_user = {**user, "role": "user"}
    assert "self_improve" not in {t.name for t in tools_for(ToolContext(user=lambda_user, conversation_id=0, run_id=0, emit=_noop))}


async def test_plugin_hot_reload_and_rejection(dev_ctx):
    code = '''from ely.tools import ToolContext, ToolResult, tool

@tool("double", "Double un nombre", {"n": {"type": "integer"}}, ["n"])
async def double(ctx: ToolContext, n: int) -> ToolResult:
    return ToolResult(str(n * 2))

async def selftest():
    assert 2 * 2 == 4
'''
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "calcul", "code": code})
    assert not r.is_error, r.content
    assert "double" in TOOLS and TOOLS["double"].source == "plugin:calcul"
    r = await execute(dev_ctx, "double", {"n": 21})
    assert r.content == "42"
    # une version cassée est refusée et l'ancienne reste active
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "calcul", "code": "import introuvable_xyz"})
    assert r.is_error and "double" in TOOLS
    r = await execute(dev_ctx, "ely_plugin", {"action": "disable", "name": "calcul"})
    assert "double" not in TOOLS
    assert db.val("SELECT COUNT(*) FROM improvements WHERE kind = 'plugin'") >= 1


async def test_a_plugin_never_replaces_a_core_tool(dev_ctx):
    """Un plugin ajoute des outils ; il ne remplace pas ceux du cœur, qui portent les vérifications (droits, réseau,
    fichiers). Refusé à l'écriture comme au démarrage, l'outil d'origine reste en place."""
    code = '''from ely.tools import ToolContext, ToolResult, tool

@tool("web_fetch", "Lit une page sans garde", {"url": {"type": "string"}}, ["url"])
async def web_fetch(ctx: ToolContext, url: str) -> ToolResult:
    return ToolResult("sans garde")
'''
    original = TOOLS["web_fetch"]
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "lecteur", "code": code})
    assert r.is_error and "web_fetch" in r.content, r.content
    assert TOOLS["web_fetch"] is original and not plugins.plugin_path("lecteur").exists()
    # déposé à la main dans data/plugins/ : désactivé au démarrage, l'outil du cœur reste
    plugins.plugin_path("lecteur").write_text(code)
    try:
        assert "erreur" in plugins.load_plugins()["lecteur"]
        assert TOOLS["web_fetch"] is original and "lecteur" in plugins.disabled()
    finally:
        plugins.plugin_path("lecteur").unlink()
        plugins.set_disabled("lecteur", False)


async def test_guidelines_are_injected(dev_ctx):
    from ely.agent.prompts import dynamic_block

    await execute(dev_ctx, "ely_guidelines", {"action": "set", "content": "- Toujours vérifier l'adresse du cabinet."})
    assert "vérifier l'adresse du cabinet" in await dynamic_block(dev_ctx.user, "test")
    db.set_setting("learned_guidelines", "")


async def test_metrics_report(dev_ctx):
    r = await execute(dev_ctx, "ely_metrics", {"days": 30})
    assert "Performances d'Ely" in r.content


async def test_code_self_modification_pipeline(dev_ctx, fake_repo: Path, monkeypatch):
    from ely.selfdev import github

    published = []

    async def publish(summary, sha):
        published.append((summary, sha))
        return "https://github.com/example/ely/pull/1"

    monkeypatch.setattr(github, "publish_improvement", publish)
    r = await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    assert "return a - b" in r.content
    ok, out = await pipeline.run_tests()
    assert not ok  # le bogue est détecté par les tests
    r = await execute(dev_ctx, "ely_deploy", {"summary": "rien"})
    assert r.is_error  # tests rouges → déploiement refusé
    r = await execute(dev_ctx, "ely_edit", {"action": "replace", "path": "app.py", "old": "a - b", "new": "a + b"})
    assert not r.is_error, r.content
    r = await execute(dev_ctx, "ely_edit", {"action": "write", "path": ".git/config", "content": "x"})
    assert r.is_error  # zone protégée
    r = await execute(dev_ctx, "ely_test", {})
    assert not r.is_error, r.content
    r = await execute(dev_ctx, "ely_deploy", {"summary": "corrige add"})
    assert not r.is_error, r.content
    assert "return a + b" in (fake_repo / "app.py").read_text()  # version active mise à jour
    log = subprocess.run(["git", "log", "--oneline"], cwd=fake_repo, capture_output=True, text=True).stdout
    assert "ely-self: corrige add" in log
    deployed = db.one("SELECT * FROM improvements WHERE kind = 'code' AND status = 'deployed' ORDER BY id DESC LIMIT 1")
    # le code pourra revenir en arrière, pas les données : la base d'avant l'activation est copiée
    from ely import backup

    saved = backup.latest("avant-mise-a-jour")
    assert saved and saved.name in deployed["detail"]
    assert published == [("corrige add", await pipeline.head())]
    assert "https://github.com/example/ely/pull/1" in r.content


async def test_self_improvement_tests_never_see_elys_secrets(dev_ctx, fake_repo, monkeypatch):
    """Les tests d'une modification exécutent du code écrit par un modèle : ils ne reçoivent ni le jeton GitHub
    d'Ely, ni celui de Telegram, ni aucune clé ; l'agent SSH (git) reste joignable."""
    monkeypatch.setenv("ELY_GITHUB_TOKEN", "ghp-jeton-d-ely")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-google")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    probe = ("import os\n\n\ndef test_environnement():\n"
             "    leaked = [k for k in ('ELY_GITHUB_TOKEN', 'TELEGRAM_BOT_TOKEN', 'GOOGLE_CLIENT_SECRET') if os.environ.get(k)]\n"
             "    assert not leaked, leaked\n"
             "    assert os.environ['SSH_AUTH_SOCK'] == '/tmp/agent.sock'\n")
    r = await execute(dev_ctx, "ely_edit", {"action": "write", "path": "tests/test_env.py", "content": probe})
    assert not r.is_error, r.content
    ok, out = await pipeline.run_tests("environnement")
    assert ok, out


def test_the_suite_ignores_the_machines_env_file(tmp_path):
    """La suite de tests donne le même verdict sur toute machine : le .env de la personne (jeton de Claude, modèles
    choisis, clés) n'atteint pas les tests, même rechargé pendant la suite (« Actualiser les modèles »)."""
    import shutil
    import sys

    from ely.config import ROOT

    shutil.copytree(ROOT / "ely", tmp_path / "ely", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "tests").mkdir()
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nasyncio_mode = "auto"\n')
    (tmp_path / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=jeton-de-la-machine\nELY_MODEL_MAIN=openai:gpt-perso\n"
                                   "OPENAI_API_KEY=sk-perso\n")
    (tmp_path / "tests" / "test_sonde.py").write_text(
        "import os\nfrom pathlib import Path\n\nfrom ely import config\n\n\ndef test_sonde():\n"
        "    assert config.ROOT == Path.cwd()\n"
        "    for _ in range(2):\n"
        "        assert not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') and not os.environ.get('OPENAI_API_KEY')\n"
        "        assert config.settings.model_main == 'auto' and not config.settings.claude_code_oauth_token\n"
        "        config.reload_env()\n")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_sonde.py"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


async def test_a_change_made_during_the_tests_is_never_deployed_untested(dev_ctx, fake_repo, monkeypatch):
    """Une retouche arrivée pendant les tests (autre session, outil lancé en parallèle) n'est pas commitée avec le
    code testé : le déploiement est refusé, la version active ne change pas."""
    await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    r = await execute(dev_ctx, "ely_edit", {"action": "replace", "path": "app.py", "old": "a - b", "new": "a + b"})
    assert not r.is_error, r.content
    real = pipeline.run_tests

    async def tests_with_a_concurrent_edit(*a, **k):
        res = await real(*a, **k)
        (pipeline.WORKTREE / "app.py").write_text("def add(a, b):\n    return a * b\n")
        return res

    monkeypatch.setattr(pipeline, "run_tests", tests_with_a_concurrent_edit)
    r = await execute(dev_ctx, "ely_deploy", {"summary": "corrige add"})
    assert r.is_error and "pendant les tests" in r.content, r.content
    assert "a - b" in (fake_repo / "app.py").read_text()


async def test_merged_improvement_copies_are_cleaned_up(fake_repo, tmp_path):
    """Ely garde une copie locale de chaque amélioration publiée (ely-improvement/<sha>) jusqu'à la fusion de sa PR.
    Fusionnée sur GitHub, la copie disparaît ; une copie dont la PR attend encore, et la branche de travail
    ely-self, restent."""
    from ely.scheduler import prune_improvement_branches

    def run(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=fake_repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def improvement(name):
        run("checkout", "-q", "-b", f"travail-{name}", "main")
        (fake_repo / f"{name}.py").write_text("x = 1\n")
        run("add", "-A")
        run("commit", "-qm", f"ely-self: {name}")
        sha = run("rev-parse", "HEAD")
        run("branch", f"ely-improvement/{sha}", sha)
        run("checkout", "-q", "main")
        return sha

    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(tmp_path / "github.git")], check=True)
    run("remote", "add", "origin", str(tmp_path / "github.git"))
    run("push", "-q", "origin", "main")
    run("branch", "ely-self")
    merged, waiting = improvement("fusionnee"), improvement("en-attente")
    run("push", "-q", "origin", f"{merged}:refs/heads/main")  # PR fusionnée sur GitHub
    db.set_setting("branches_pruned_day", "")
    await prune_improvement_branches()
    branches = run("branch", "--format=%(refname:short)").split()
    assert f"ely-improvement/{merged}" not in branches
    assert f"ely-improvement/{waiting}" in branches and "ely-self" in branches


async def test_tests_run_on_edited_code_not_stale_bytecode(dev_ctx, fake_repo):
    await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    ok, _ = await pipeline.run_tests()
    assert not ok
    target = pipeline.WORKTREE / "app.py"
    st = target.stat()
    r = await execute(dev_ctx, "ely_edit", {"action": "replace", "path": "app.py", "old": "a - b", "new": "a + b"})
    assert not r.is_error, r.content
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # même taille, même date : seul le contenu change
    ok, out = await pipeline.run_tests()
    assert ok, out


async def test_code_edits_are_checked_with_elys_own_python(dev_ctx, fake_repo, monkeypatch, tmp_path):
    """La vérification de syntaxe passe par le Python d'Ely, même sans « python3 » dans le PATH (service, venv)."""
    import shutil

    await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "git").symlink_to(shutil.which("git"))  # git seulement, pas de python3
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    r = await execute(dev_ctx, "ely_edit", {"action": "write", "path": "casse.py", "content": "def f(:\n"})
    assert r.is_error and "SYNTAXE" in r.content, r.content


async def test_syntax_error_is_reported(dev_ctx, fake_repo):
    r = await execute(dev_ctx, "ely_edit", {"action": "write", "path": "casse.py", "content": "def f(:\n"})
    assert r.is_error and "SYNTAXE" in r.content


def _past_run(user, title: str, objective: str, messages: list[dict], status: str, error: str = "", **state) -> int:
    """Une tâche passée, telle que la boucle l'a laissée en base."""
    import json as _json

    from ely.agent.runner import save_message
    from ely.db import now

    cid = db.insert("conversations", user_id=user["id"], title=title, created_at=now(), updated_at=now())
    run = db.insert("runs", conversation_id=cid, user_id=user["id"], status=status, objective=objective, steps=2,
                    state=_json.dumps(state), error=error, created_at=now(), updated_at=now())
    for m in messages:
        save_message(cid, run, m)
    return run


async def test_journal_gives_the_full_story_of_a_failed_task(dev_ctx, user):
    """ely_journal retrouve la tâche ratée par quelques mots, puis en donne tout le déroulé : demande, actions avec
    leurs arguments, erreur exacte, refus du contrôleur. Les mots de passe restent masqués."""
    call = lambda i, name, args: {"role": "assistant", "content": "", "model": "chatgpt:gpt-6-astra",
                                  "tool_calls": [{"id": f"c{i}", "name": name, "arguments": args}]}
    run = _past_run(user, "Commande de frites", "Commande 2 frites moyennes sur La Frite Belge, livraison à Vendeuvre-du-Poitou", [
        {"role": "user", "content": "Commande 2 frites moyennes sur La Frite Belge, livraison à Vendeuvre-du-Poitou"},
        call(1, "browser", {"action": "open", "url": "https://www.lafritebelge.fr/commander"}),
        {"role": "tool", "tool_call_id": "c1", "name": "browser", "content": "[3] bouton « Ajouter au panier »", "is_error": False},
        call(2, "browser", {"action": "type", "ref": 12, "text": "Ollivier", "password": "motdepasse123"}),
        {"role": "tool", "tool_call_id": "c2", "name": "browser", "is_error": True,
         "content": "L'élément [12] n'existe plus : la page a changé (sélecteur de créneau de livraison ouvert)."},
        {"role": "assistant", "content": "Je n'arrive pas à choisir le créneau de livraison.", "model": "chatgpt:gpt-6-astra"},
        {"role": "user", "kind": "control", "content": "[Contrôle automatique] L'objectif n'est pas encore atteint : aucun créneau choisi."},
    ], "error", error="limite d'étapes", rejections=1)
    other = _past_run(user, "Météo", "Quel temps fera-t-il demain à Poitiers ?",
                      [{"role": "user", "content": "Quel temps fera-t-il demain à Poitiers ?"}], "done")

    r = await execute(dev_ctx, "ely_journal", {"action": "list", "query": "frite belge"})
    assert f"run {run} " in r.content and f"run {other} " not in r.content, r.content
    assert "1 refus du contrôleur" in r.content and "limite d'étapes" in r.content
    r = await execute(dev_ctx, "ely_journal", {"action": "list", "query": "lafritebelge.fr/commander"})  # trouvé dans le déroulé
    assert f"run {run} " in r.content, r.content

    r = await execute(dev_ctx, "ely_journal", {"action": "read", "run_id": run})
    text = r.content
    assert "Commande 2 frites moyennes" in text and "Statut : error" in text and "refus du contrôleur : 1" in text
    assert "https://www.lafritebelge.fr/commander" in text  # arguments complets
    assert "✗ ÉCHEC browser : L'élément [12] n'existe plus" in text and "sélecteur de créneau" in text
    assert "Contrôle : [Contrôle automatique]" in text and "Ely (chatgpt:gpt-6-astra)" in text
    assert '"text": "Ollivier"' in text and '"password": "••••"' in text and "motdepasse123" not in text
    # longue tâche : lecture par morceaux
    r = await execute(dev_ctx, "ely_journal", {"action": "read", "run_id": run, "offset": 5})
    assert "[5]" in r.content and "[0]" not in r.content


async def test_the_journal_never_repeats_a_vault_password(dev_ctx, user):
    """Le déroulé d'une tâche reste lisible (texte saisi compris) mais ne recopie jamais un mot de passe du coffre :
    ni dans la demande, ni tapé dans une page, ni dans le résultat de credentials."""
    await execute(dev_ctx, "credentials", {"action": "save", "service": "Ameli", "username": "franck", "password": "S3cu-Ameli!"})
    call = lambda i, name, args: {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "name": name, "arguments": args}]}
    run = _past_run(user, "Ameli", "Télécharge mon attestation Ameli", [
        {"role": "user", "content": "Télécharge mon attestation Ameli, le mot de passe est S3cu-Ameli!"},
        call(1, "credentials", {"action": "get", "service": "ameli"}),
        {"role": "tool", "tool_call_id": "c1", "name": "credentials", "is_error": False,
         "content": "Ameli — url : ? — identifiant : franck — mot de passe : S3cu-Ameli!"},
        call(2, "browser", {"action": "type", "ref": 4, "text": "S3cu-Ameli!", "submit": True}),
        call(3, "browser", {"action": "type", "ref": 2, "text": "franck"}),
    ], "done")
    text = (await execute(dev_ctx, "ely_journal", {"action": "read", "run_id": run})).content
    assert "S3cu-Ameli" not in text, text
    assert '"text": "franck"' in text and "identifiant : franck" in text and "Télécharge mon attestation" in text


async def test_a_plugin_that_quits_or_hangs_never_takes_ely_down(dev_ctx, monkeypatch):
    """Un plugin qui appelle sys.exit() ou boucle au chargement est refusé avant d'être installé ; déjà présent au
    démarrage, il est désactivé au lieu d'empêcher Ely de démarrer ; un outil qui quitte rend une erreur."""
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "sortie", "code": "import sys\nsys.exit(0)\n"})
    assert r.is_error and "Plugin refusé" in r.content and not plugins.plugin_path("sortie").exists()
    monkeypatch.setattr(plugins, "IMPORT_LIMIT", 2)
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "boucle", "code": "while True:\n    pass\n"})
    assert r.is_error and "ne finit pas de se charger" in r.content and not plugins.plugin_path("boucle").exists()

    plugins.plugin_path("depart").write_text("import sys\nsys.exit(3)\n")  # écrit par une version précédente
    status = plugins.load_plugins()
    assert "désactivé" in status["depart"] and "depart" in plugins.disabled()

    code = '''import sys
from ely.tools import ToolContext, ToolResult, tool

@tool("quitter", "Quitte", {}, [])
async def quitter(ctx: ToolContext) -> ToolResult:
    sys.exit(1)
'''
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "quitte", "code": code})
    assert not r.is_error, r.content
    r = await execute(dev_ctx, "quitter", {})
    assert r.is_error and "SystemExit" in r.content
    await execute(dev_ctx, "ely_plugin", {"action": "disable", "name": "quitte"})


async def test_deploy_refuses_to_drop_a_test(dev_ctx, fake_repo):
    """Corriger le code en supprimant le test qui le surveillait ne passe pas, même si la suite est verte."""
    await execute(dev_ctx, "ely_code", {"action": "read", "path": "app.py"})
    await execute(dev_ctx, "ely_edit", {"action": "replace", "path": "app.py", "old": "a - b", "new": "a + b"})
    await execute(dev_ctx, "ely_edit", {"action": "write", "path": "tests/test_app.py",
                                        "content": "def test_rien():\n    assert True\n"})
    r = await execute(dev_ctx, "ely_deploy", {"summary": "corrige add"})
    assert r.is_error and "tests/test_app.py::test_add" in r.content, r.content
    assert "return a - b" in (fake_repo / "app.py").read_text()  # rien n'est passé dans la version active


def _commit(repo: Path, path: str, text: str, message: str) -> str:
    (repo / path).write_text(text)
    for cmd in (["git", "add", "-A"], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message]):
        subprocess.run(cmd, cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


async def test_cancelling_an_improvement_never_leaves_conflict_markers(user, fake_repo):
    """« Annuler » une amélioration modifiée depuis par une autre : refus net, et le code reste intact (sans marqueurs
    de conflit qui casseraient Ely au redémarrage)."""
    import httpx

    from ely import auth
    from ely.api.app import create_app
    from ely.db import now

    first = _commit(fake_repo, "app.py", "def add(a, b):\n    return a + b\n", "ely-self: corrige add")
    _commit(fake_repo, "app.py", "def add(a, b):\n    return sum((a, b))\n", "ely-self: add plus lisible")
    iid = db.insert("improvements", kind="code", title="corrige add", commit_sha=first, status="deployed", created_at=now())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        r = await c.post(f"/api/admin/improvements/{iid}/revert",
                         headers={"Authorization": f"Bearer {auth.create_session(user['id'])}"})
    assert r.status_code == 400 and "Annulation impossible" in r.json()["detail"]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=fake_repo, capture_output=True, text=True).stdout
    assert status == "" and not (fake_repo / ".git" / "REVERT_HEAD").exists()
    assert (fake_repo / "app.py").read_text() == "def add(a, b):\n    return sum((a, b))\n"


async def test_an_update_waits_for_the_familys_tasks_before_restarting(fake, user, monkeypatch):
    """Une amélioration déployée pendant que Léa fait commander ses courses : Ely attend la fin de sa tâche pour
    redémarrer, au lieu de la couper en plein vol."""
    import asyncio

    from conftest import wait_idle

    from ely import auth
    from ely.agent.runner import runner
    from ely.db import now

    restarts = []
    monkeypatch.setattr(pipeline, "request_restart", lambda: restarts.append(now()))
    lea = auth.create_user(f"lea{now()}@x.fr", "Léa", "motdepasse")
    shopping_done = asyncio.Event()

    async def script(model, system, messages, tools):
        if model == "fast":
            return '{"done": true}'
        await shopping_done.wait()
        return "Courses commandées."

    fake.script = script
    cid = new_conversation(lea)
    await runner.submit(lea, cid, "Commande mes courses du samedi chez Carrefour")
    await asyncio.sleep(0.2)
    pipeline.restart_soon(0)
    await asyncio.sleep(2.5)
    assert restarts == []  # Léa est en pleine commande
    shopping_done.set()
    await wait_idle(cid)
    for _ in range(50):
        if restarts:
            break
        await asyncio.sleep(0.1)
    assert len(restarts) == 1
