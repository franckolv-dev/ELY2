"""Le superviseur et Python doivent tester le même port, même si la saisie est invalide."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ely.db import db
from ely.selfdev import github, pipeline

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("environment,file_value,expected", [
    (None, None, 8000),
    (None, "800O", 8000),
    ("", "9123", 8000),
    ("800O", "9123", 8000),
    ("8123", "9123", 8123),
    (None, "'  8123  '", 8123),
    (None, '"9123" # commentaire', 9123),
    ("008000", None, 8000),
    ("+8000", None, 8000),
    ("8_000", None, 8000),
])
def test_shell_port_matches_python_conversion(tmp_path, environment, file_value, expected):
    # Extraire les fonctions réelles sans démarrer de service ni faire d'installation.
    source, dispatch = (ROOT / "ely.sh").read_text().split('case "${1:-start}" in', 1)
    assert 'start) start' in dispatch
    script = tmp_path / "ely.sh"
    script.write_text(source + '\nelyport\n')
    binary = tmp_path / ".venv" / "bin"
    binary.mkdir(parents=True)
    (binary / "python").symlink_to(sys.executable)
    if file_value is not None:
        (tmp_path / ".env").write_text(f"ELY_PORT={file_value}\n")
    env = {k: v for k, v in os.environ.items() if k != "ELY_PORT"}
    if environment is not None:
        env["ELY_PORT"] = environment
    result = subprocess.run(["bash", str(script)], env=env, text=True,
                            capture_output=True, timeout=5, check=True)
    assert result.stdout.strip() == str(expected), result.stderr


@pytest.mark.parametrize("restart", [True, False])
async def test_launcher_change_is_published_without_activation(fake_repo, monkeypatch, restart):
    await pipeline.prepare()
    target = pipeline.WORKTREE / "app.py"
    target.write_text(target.read_text().replace("a - b", "a + b"))
    (pipeline.WORKTREE / "ely.sh").write_text("#!/bin/sh\necho launcher-fix\n")
    previous = await pipeline.head()
    calls = []
    publications = []

    async def publish(summary, sha):
        publications.append(sha)
        return "https://github.com/example/ely/pull/1"

    monkeypatch.setattr(github, "publish_improvement", publish)
    monkeypatch.setattr(pipeline, "restart_soon", lambda *a, **kw: calls.append("restart"))
    monkeypatch.setenv("ELY_SUPERVISED", "1")
    marker = pipeline.settings.data_dir / "selfdev" / "last_deploy.json"
    before = marker.read_bytes() if marker.exists() else None
    result = await pipeline.deploy("Lanceur corrigé", restart=restart)

    assert result["ok"]
    assert result["activated"] is False and result["restart"] is False
    assert publications == [result["sha"]]
    assert result["pr_url"] == "https://github.com/example/ely/pull/1"
    assert await pipeline.head() == previous
    assert "a - b" in (fake_repo / "app.py").read_text()
    assert not (fake_repo / "ely.sh").exists()
    assert not calls
    assert (marker.read_bytes() if marker.exists() else None) == before
    row = db.one("SELECT * FROM improvements WHERE commit_sha = ?", (result["sha"],))
    assert row["status"] == "published"


async def test_unverifiable_changed_files_prevent_activation(fake_repo, monkeypatch):
    await pipeline.prepare()
    target = pipeline.WORKTREE / "app.py"
    target.write_text(target.read_text().replace("a - b", "a + b"))
    previous = await pipeline.head()
    real_git = pipeline.git

    async def git(*args, **kwargs):
        if args[:2] == ("diff", "--name-only"):
            return 1, "synthetic diff failure"
        return await real_git(*args, **kwargs)

    async def publish(*args):
        return "https://github.com/example/ely/pull/1"

    monkeypatch.setattr(github, "publish_improvement", publish)
    monkeypatch.setattr(pipeline, "git", git)
    result = await pipeline.deploy("Correction", restart=False)
    assert not result["ok"]
    assert result["activated"] is False and result["restart"] is False
    assert "Aucune activation locale" in result["message"]
    assert await pipeline.head() == previous
