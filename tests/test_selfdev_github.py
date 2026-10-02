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
