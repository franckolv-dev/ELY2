"""GitHub simulé : publication obligatoire, attribution, reprise sans doublon, aucun merge distant."""
import json

import pytest

from ely.selfdev import github, pipeline

SHA = "a" * 40
BRANCH = f"ely-improvement/{SHA}"
URL = "https://github.com/example/ely/pull/7"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("remote", ["https://github.com/example/ely.git", "git@github.com:example/ely.git"])
async def test_publish_tested_commit_and_verify_pr(monkeypatch, existing, remote):
    calls = []

    async def git(*args, **kwargs):
        calls.append(("git", args))
        if args[0] == "rev-parse":
            return (0, SHA) if existing else (1, "")
        if args[0] == "remote":
            return 0, remote
        return 0, ""

    async def gh(*args):
        calls.append(("gh", args))
        if args[:2] == ("repo", "view"):
            return "main"
        if args[:2] == ("pr", "list"):
            return json.dumps([{"url": URL}] if existing else [])
        if args[:2] == ("pr", "create"):
            body = args[args.index("--body") + 1]
            assert "Ely" in body and "auto-amélioration" in body and SHA in body
            return URL
        if args[:2] == ("pr", "view"):
            return json.dumps({"url": URL, "state": "OPEN", "headRefOid": SHA,
                               "headRefName": BRANCH, "baseRefName": "main"})
        raise AssertionError(args)

    monkeypatch.setattr(pipeline, "git", git)
    monkeypatch.setattr(github, "gh", gh)
    assert await github.publish_improvement("Correction testée", SHA) == URL
    assert ("git", ("push", "origin", f"{SHA}:refs/heads/{BRANCH}")) in calls
    creates = [a for tool, a in calls if tool == "gh" and a[:2] == ("pr", "create")]
    assert len(creates) == (0 if existing else 1)
    assert not any("--force" in a or "merge" in a for _, a in calls)


async def test_push_failure_keeps_backup_and_does_not_create_pr(monkeypatch):
    calls = []

    async def git(*args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return 1, ""
        if args[0] == "remote":
            return 0, "https://github.com/example/ely.git"
        return (1, "refused") if args[0] == "push" else (0, "")

    async def gh(*args):
        assert args[:2] == ("repo", "view")  # aucune création de PR après le push refusé
        return "main"

    monkeypatch.setattr(pipeline, "git", git)
    monkeypatch.setattr(github, "gh", gh)
    with pytest.raises(RuntimeError, match="Push GitHub refusé"):
        await github.publish_improvement("Correction", SHA)
    assert ("branch", BRANCH, SHA) in calls


async def test_pr_for_wrong_commit_is_rejected(monkeypatch):
    async def git(*args, **kwargs):
        if args[0] == "rev-parse":
            return 0, SHA
        if args[0] == "remote":
            return 0, "https://github.com/example/ely.git"
        return 0, ""

    async def gh(*args):
        if args[:2] == ("repo", "view"):
            return "main"
        if args[:2] == ("pr", "list"):
            return json.dumps([{"url": URL}])
        return json.dumps({"url": URL, "state": "OPEN", "headRefOid": "b" * 40,
                           "headRefName": BRANCH, "baseRefName": "main"})

    monkeypatch.setattr(pipeline, "git", git)
    monkeypatch.setattr(github, "gh", gh)
    with pytest.raises(RuntimeError, match="commit testé"):
        await github.publish_improvement("Correction", SHA)


async def test_publication_failure_prevents_local_activation(fake_repo, monkeypatch):
    await pipeline.prepare()
    target = pipeline.WORKTREE / "app.py"
    target.write_text(target.read_text().replace("a - b", "a + b"))
    previous = await pipeline.head()

    async def fail(*args):
        raise RuntimeError("GitHub indisponible")

    monkeypatch.setattr(github, "publish_improvement", fail)
    result = await pipeline.deploy("Correction", restart=False)
    assert not result["ok"]
    assert "Aucune activation locale" in result["message"]
    assert "GitHub indisponible" in result["message"]
    assert await pipeline.head() == previous
    assert "a - b" in (fake_repo / "app.py").read_text()


FAKE_GH = r'''#!{python}
"""Faux CLI gh : note le jeton reçu ; le compte d'Ely s'appelle ely-assistante (id 4242)."""
import json, os, sys
with open({log!r}, "a") as f:
    f.write(json.dumps({{"args": sys.argv[1:], "token": os.environ.get("GH_TOKEN")}}) + "\n")
if sys.argv[1:3] == ["api", "user"]:
    if os.environ.get("GH_TOKEN") != "ghp-ely":
        sys.stderr.write("HTTP 401: Bad credentials\n"); sys.exit(1)
    print(json.dumps({{"login": "ely-assistante", "id": 4242}}))
'''


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    import os
    import sys

    log = tmp_path / "gh.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(python=sys.executable, log=str(log)))
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


async def test_ely_signs_and_publishes_with_its_own_github_account(fake_repo, fake_gh, monkeypatch):
    """Ely a son compte GitHub (invité sur le dépôt) : ses commits portent l'adresse noreply de ce compte, ce qui la
    fait figurer parmi les contributeurs, et gh agit avec son jeton, pas avec la connexion de l'administrateur."""
    import subprocess

    from ely.config import settings

    monkeypatch.setattr(settings, "ely_github_token", "ghp-ely")
    published = []

    async def publish(summary, sha):
        published.append(sha)
        await github.gh("pr", "list")  # comme la vraie publication : gh, avec le jeton d'Ely
        return URL

    monkeypatch.setattr(github, "publish_improvement", publish)
    await pipeline.prepare()
    target = pipeline.WORKTREE / "app.py"
    target.write_text(target.read_text().replace("a - b", "a + b"))
    result = await pipeline.deploy("Correction de l'addition", restart=False)
    assert result["ok"], result
    author = subprocess.run(["git", "log", "-1", "--format=%an <%ae>", published[0]], cwd=fake_repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "Ely <4242+ely-assistante@users.noreply.github.com>"
    assert [c["token"] for c in fake_gh()] == ["ghp-ely", "ghp-ely"]


async def test_a_refused_ely_github_token_stops_before_any_commit(fake_repo, fake_gh, monkeypatch):
    """Jeton du compte d'Ely refusé par GitHub (expiré, révoqué) : rien n'est commité ni activé, et Ely dit quoi
    vérifier. Sans jeton, les commits restent signés « Ely <ely@localhost> », par la connexion gh du Mac."""
    from ely.config import settings

    monkeypatch.setattr(settings, "ely_github_token", "ghp-expire")
    await pipeline.prepare()
    target = pipeline.WORKTREE / "app.py"
    target.write_text(target.read_text().replace("a - b", "a + b"))
    previous = await pipeline.head()
    result = await pipeline.deploy("Correction", restart=False)
    assert not result["ok"] and "ELY_GITHUB_TOKEN" in result["message"] and "Aucun commit" in result["message"]
    assert (await pipeline.git("rev-parse", "HEAD", cwd=pipeline.WORKTREE))[1] == previous
    assert await pipeline.head() == previous

    monkeypatch.setattr(settings, "ely_github_token", "")
    assert await github.author() == ("Ely", "ely@localhost")
    await github.gh("pr", "list")
    assert fake_gh()[-1]["token"] is None
