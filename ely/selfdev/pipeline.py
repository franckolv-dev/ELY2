"""Modification du propre code d'Ely, en sécurité.

1. Ely travaille dans une copie git isolée (worktree, branche ely-self).
2. La suite de tests doit passer dans cette copie.
3. Les changements sont validés (commit), sauvegardés sur une branche GitHub avec une PR
   à fusionner par l'administrateur, puis fusionnés dans la version active locale.
4. Ely redémarre (code de sortie 42) ; le lanceur vérifie la santé de la nouvelle
   version et revient automatiquement à la précédente en cas de problème.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ..config import ROOT, settings
from ..db import db, now

WORKTREE = settings.data_dir / "selfdev" / "worktree"
BRANCH = "ely-self"
RESTART_CODE = 42
RESTART_DELAY = 4  # secondes laissées à la réponse en cours avant de redémarrer
RESTART_WAIT = 15 * 60  # au plus : les tâches en cours des autres conversations se terminent avant le redémarrage
PROTECTED = {".git", ".env", "data", ".venv"}


async def git(*args: str, cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=str(cwd or ROOT), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode, out.decode(errors="replace").strip()


async def is_repo() -> bool:
    code, _ = await git("rev-parse", "--is-inside-work-tree")
    return code == 0


async def head() -> str:
    return (await git("rev-parse", "HEAD"))[1]


async def prepare() -> str:
    """Prépare (ou remet à zéro) la copie de travail sur la version active."""
    if not await is_repo():
        raise RuntimeError(f"{ROOT} n'est pas un dépôt git : l'auto-modification du code est impossible "
                           "(les plugins, compétences et leçons restent disponibles).")
    sha = await head()
    if not (WORKTREE / ".git").exists():
        WORKTREE.parent.mkdir(parents=True, exist_ok=True)
        await git("worktree", "prune")
        code, out = await git("worktree", "add", "-f", "-B", BRANCH, str(WORKTREE), sha)
        if code:
            raise RuntimeError(f"création du worktree impossible : {out}")
    else:
        await git("checkout", "-f", "-B", BRANCH, sha, cwd=WORKTREE)
        await git("reset", "--hard", sha, cwd=WORKTREE)
        await git("clean", "-fd", cwd=WORKTREE)
    return sha


async def ensure_session() -> None:
    """Crée la copie si besoin, sans écraser un travail en cours.

    Si la copie contient des commits absents de la version active (retour arrière effectué par le
    lanceur, fusion refusée), elle est remise à niveau : on ne redéploie jamais une version rejetée."""
    if not (WORKTREE / ".git").exists():
        await prepare()
        return
    code, out = await git("rev-list", "--count", f"{await head()}..{BRANCH}")
    if code or out.strip() != "0":
        await prepare()


def resolve(path: str) -> Path:
    p = (WORKTREE / path).resolve()
    if not p.is_relative_to(WORKTREE.resolve()):
        raise ValueError("chemin hors du code d'Ely")
    rel = p.relative_to(WORKTREE.resolve())
    if rel.parts and rel.parts[0] in PROTECTED:
        raise ValueError(f"chemin protégé : {rel.parts[0]}")
    return p


async def diff() -> str:
    await git("add", "-A", cwd=WORKTREE)
    return (await git("diff", "--cached", "--stat", cwd=WORKTREE))[1] + "\n\n" + (await git("diff", "--cached", cwd=WORKTREE))[1][:30000]


def _test_env(data: str, code: Path) -> dict:
    # Pas de .pyc : une retouche de même taille dans la même seconde serait testée sur l'ancien bytecode
    env = {**os.environ, "ELY_DATA_DIR": data, "PYTHONPATH": str(code), "PYTHONDONTWRITEBYTECODE": "1"}
    for k in list(env):  # les tests n'appellent jamais de vrais modèles
        if k.endswith("_API_KEY") or k == "CLAUDE_CODE_OAUTH_TOKEN":
            env.pop(k)
    return env


async def collect(code: Path) -> set[str]:
    """Identifiants des tests d'une version du code (vide si la collecte échoue)."""
    tmp = tempfile.mkdtemp(prefix="ely-collect-")
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                                                cwd=str(code), env=_test_env(tmp, code), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 300)
    except asyncio.TimeoutError:
        proc.kill()
        return set()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {line.strip() for line in out.decode(errors="replace").splitlines() if "::" in line and " " not in line.strip()}


async def removed_tests() -> list[str]:
    """Tests de la version active absents de la copie de travail : un test ne se supprime pas pour faire passer un
    changement (la règle ne dépend pas de la bonne volonté du modèle)."""
    active = await collect(ROOT)
    return sorted(active - await collect(WORKTREE)) if active else []


async def run_tests(pattern: str = "", timeout: int = 900) -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="ely-test-")
    env = _test_env(tmp, WORKTREE)
    args = [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
    if pattern:
        args += ["-k", pattern]
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(WORKTREE), env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"tests interrompus après {timeout} s"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = out.decode(errors="replace")
    return proc.returncode == 0, text[-6000:]


async def deploy(summary: str, restart: bool = True) -> dict:
    """Tests, commit, fusion dans la version active. Sous le lanceur, Ely redémarre aussitôt ; avec restart=False,
    c'est à l'appelant de le demander (session Claude : à la fin de la mission, pas au milieu)."""
    ok, out = await run_tests()
    if not ok:
        return {"ok": False, "message": "Les tests échouent, déploiement refusé :\n" + out[-3000:]}
    gone = await removed_tests()
    if gone:
        return {"ok": False, "message": "Déploiement refusé : des tests de la version active ont disparu (supprimés ou "
                "renommés). Rétablissez-les ; pour changer un comportement, adaptez le test sans le retirer :\n"
                + "\n".join(f"- {t}" for t in gone[:20])}
    await git("add", "-A", cwd=WORKTREE)
    code, st = await git("diff", "--cached", "--quiet", cwd=WORKTREE)
    if code == 0:
        return {"ok": False, "message": "Aucune modification à déployer."}
    patch = (await git("diff", "--cached", cwd=WORKTREE))[1][:50000]
    from . import github

    try:
        name, email = await github.author()
    except RuntimeError as exc:
        return {"ok": False, "message": f"{exc} Aucun commit créé, rien n'est activé."}
    code, out = await git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", f"ely-self: {summary}", cwd=WORKTREE)
    if code:
        return {"ok": False, "message": f"commit impossible : {out}"}
    new_sha = (await git("rev-parse", "HEAD", cwd=WORKTREE))[1]
    from .github import publish_improvement

    try:
        pr_url = await publish_improvement(summary, new_sha)
    except Exception as exc:
        return {"ok": False, "sha": new_sha,
                "message": f"Commit {new_sha} créé, mais publication GitHub non confirmée : {exc}. "
                           "Aucune activation locale. Vérifier la branche ely-improvement/" + new_sha + "."}
    prev = await head()
    code, out = await git("merge", "--ff-only", BRANCH)
    if code:
        return {"ok": False, "pr_url": pr_url,
                "message": f"PR publiée : {pr_url}. Fusion locale impossible (modifications locales ?) : {out}. "
                           "Demander à l'administrateur de relire et fusionner la PR."}
    db.insert("improvements", kind="code", title=summary[:200], detail=f"{prev[:10]} → {new_sha[:10]} · PR : {pr_url}", diff=patch,
              commit_sha=new_sha, status="deployed", created_at=now())
    (settings.data_dir / "selfdev").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "selfdev" / "last_deploy.json").write_text(json.dumps({"prev": prev, "new": new_sha, "summary": summary, "at": now()}))
    supervised = os.environ.get("ELY_SUPERVISED") == "1"
    if supervised and restart:
        restart_soon()
    if not supervised:
        after = "Redémarre Ely pour activer la nouvelle version."
    elif restart:
        after = "Redémarrage automatique dans quelques secondes (retour arrière automatique si problème)."
    else:
        after = "Ely redémarrera à la fin de cette session pour l'activer (retour arrière automatique si problème)."
    return {"ok": True, "sha": new_sha, "restart": supervised, "pr_url": pr_url,
            "message": "Déployé localement. " + after + f" PR GitHub ouverte : {pr_url}. "
                       "Demander à l'administrateur de la relire et de la fusionner ; ne pas la fusionner automatiquement."}


_waiting: set[asyncio.Task] = set()


def restart_soon(delay: float | None = None) -> None:
    """Redémarrage pour activer une nouvelle version, quand les tâches des autres conversations sont finies."""
    def start() -> None:
        task = asyncio.ensure_future(restart_when_idle())
        _waiting.add(task)
        task.add_done_callback(_waiting.discard)

    asyncio.get_running_loop().call_later(RESTART_DELAY if delay is None else delay, start)


async def restart_when_idle() -> None:
    """Attend (au plus RESTART_WAIT) que personne n'ait de tâche en plein travail hors auto-amélioration : une routine
    ou la demande d'un membre de la famille n'est pas coupée par la mise à jour d'Ely."""
    from ..agent.runner import runner

    end = time.monotonic() + RESTART_WAIT
    while runner.working(except_channel="selfdev") and time.monotonic() < end:
        await asyncio.sleep(2)
    request_restart()


def request_restart() -> None:
    """Demande au lanceur de redémarrer Ely (les tâches en cours seront reprises)."""
    import signal

    (settings.data_dir / "selfdev").mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "selfdev" / "restart_requested").write_text(str(now()))
    os.kill(os.getpid(), signal.SIGTERM)


def check_rollback() -> None:
    """Au démarrage : consigne un éventuel retour arrière effectué par le lanceur."""
    p = settings.data_dir / "selfdev" / "rollback.json"
    if p.exists():
        try:
            info = json.loads(p.read_text())
            db.insert("improvements", kind="code", title=f"Retour arrière automatique : {info.get('reason', 'démarrage en échec')}",
                      detail=json.dumps(info)[:2000], commit_sha=info.get("bad", ""), status="rolled_back", created_at=now())
        finally:
            p.unlink(missing_ok=True)
