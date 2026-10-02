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
    assert db.one("SELECT * FROM improvements WHERE kind = 'code' AND status = 'deployed'")
    assert published == [("corrige add", await pipeline.head())]
    assert "https://github.com/example/ely/pull/1" in r.content


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
