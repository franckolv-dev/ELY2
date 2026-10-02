"""Publication des auto-améliorations sur GitHub, sans fusionner la PR."""
from __future__ import annotations

import asyncio
import json
import re

from . import pipeline


async def gh(*args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args, cwd=str(pipeline.WORKTREE),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI (gh) absent : installer gh puis s'authentifier.") from exc
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.communicate()
        raise
    if proc.returncode:
        # Les erreurs de CLI peuvent contenir des informations de connexion.
        raise RuntimeError(f"GitHub CLI a échoué ({' '.join(args[:2])}) ; vérifier gh auth status et l'accès au dépôt.")
    return out.decode().strip()


async def publish_improvement(summary: str, sha: str) -> str:
    """Sauvegarde une branche immuable, pousse le commit testé et crée/réutilise une PR.

    L'origine Git désigne explicitement le dépôt ; jamais de push sur main ni de force-push.
    La branche locale de sauvegarde survit à la remise à zéro du worktree ely-self.
    """
    branch = f"ely-improvement/{sha}"
    code, saved = await pipeline.git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=pipeline.WORKTREE)
    if code:
        code, _ = await pipeline.git("branch", branch, sha, cwd=pipeline.WORKTREE)
        if code:
            raise RuntimeError("Impossible de sauvegarder la branche d'auto-amélioration.")
    elif saved != sha:
        raise RuntimeError("La branche de sauvegarde désigne un autre commit : publication arrêtée.")

    code, remote = await pipeline.git("remote", "get-url", "--push", "origin", cwd=pipeline.WORKTREE)
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote)
    if code or not match:
        raise RuntimeError("origin doit désigner explicitement un dépôt github.com (HTTPS ou SSH).")
    repo = match.group(1)
    base = await gh("repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name")
    if not base or base == "null":
        raise RuntimeError("Branche principale GitHub introuvable.")
    code, _ = await pipeline.git("push", "origin", f"{sha}:refs/heads/{branch}", cwd=pipeline.WORKTREE)
    if code:
        raise RuntimeError(f"Push GitHub refusé ; commit conservé dans la branche locale {branch}.")

    prs = json.loads(await gh("pr", "list", "--repo", repo, "--head", branch, "--base", base,
                              "--state", "open", "--json", "url"))
    if prs:
        url = prs[0]["url"]
    else:
        body = ("## Origine\nCette amélioration a été créée par **Ely**, l'agent personnel, "
                "dans le cadre de son **auto-amélioration**, à la demande de l'administrateur.\n\n"
                f"## Modification\n{summary}\n\n## Vérification\n"
                "La suite de tests a réussi avant la création du commit (certains tests peuvent être ignorés "
                "selon l'environnement). Le résultat détaillé figure dans le compte rendu de la session.\n\n"
                f"Commit testé : `{sha}`.\n\n## Fusion\n"
                "PR à relire et à fusionner par l'administrateur. Ely ne la fusionne pas automatiquement.\n")
        url = await gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
                       "--title", f"[Ely · auto-amélioration] {summary}"[:200], "--body", body)
    pr = json.loads(await gh("pr", "view", url, "--repo", repo, "--json",
                             "url,state,headRefOid,headRefName,baseRefName"))
    if (pr["state"] != "OPEN" or pr["headRefOid"] != sha or pr["headRefName"] != branch
            or pr["baseRefName"] != base or not pr["url"].startswith(f"https://github.com/{repo}/pull/")):
        raise RuntimeError("La PR distante ne correspond pas au commit testé : vérifier GitHub avant de poursuivre.")
    return pr["url"]
