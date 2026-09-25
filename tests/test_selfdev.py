"""Auto-amélioration : plugins à chaud, métriques, modification du code avec tests et déploiement."""
from __future__ import annotations

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
    assert {"ely_edit", "ely_test", "ely_deploy", "ely_plugin", "ely_metrics"} <= dev
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


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    monkeypatch.setattr(pipeline, "ROOT", repo)
    monkeypatch.setattr(pipeline, "WORKTREE", tmp_path / "wt")
    return repo


async def test_code_self_modification_pipeline(dev_ctx, fake_repo: Path):
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


async def test_syntax_error_is_reported(dev_ctx, fake_repo):
    r = await execute(dev_ctx, "ely_edit", {"action": "write", "path": "casse.py", "content": "def f(:\n"})
    assert r.is_error and "SYNTAXE" in r.content
