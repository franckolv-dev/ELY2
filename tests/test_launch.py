"""Démarrage : un port déjà occupé donne un message clair et un arrêt net (pas de relance en boucle)."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from ely.__main__ import PORT_BUSY, check_port

ROOT = Path(__file__).resolve().parent.parent


def busy_socket() -> tuple[socket.socket, int]:
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_free_port_is_ok():
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()
    assert check_port("0.0.0.0", port) == ""


def test_port_taken_by_another_program():
    s, port = busy_socket()
    try:
        msg = check_port("0.0.0.0", port)
        assert f"Le port {port} est déjà utilisé" in msg and "ELY_PORT" in msg and "docker compose down" in msg
    finally:
        s.close()


def test_port_taken_by_a_running_ely():
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": "4.0.0", "code": "4.0.0 · 537d416 (29/09/2026)", "tools": 37}

    s, port = busy_socket()
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        msg = check_port("0.0.0.0", port)
        assert "Ely tourne déjà" in msg and f"http://localhost:{port}" in msg
    finally:
        srv.should_exit = True


def test_launch_exits_with_code_3_when_port_is_busy(tmp_path):
    s, port = busy_socket()
    try:
        env = {**os.environ, "ELY_PORT": str(port), "ELY_DATA_DIR": str(tmp_path)}
        r = subprocess.run([sys.executable, "-m", "ely"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == PORT_BUSY
        assert "Ely ne peut pas démarrer" in r.stderr and f"port {port}" in r.stderr
    finally:
        s.close()


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.fr", "GIT_COMMITTER_NAME": "Test",
           "GIT_COMMITTER_EMAIL": "t@t.fr"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


def _update(mac: Path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.fr", "GIT_COMMITTER_NAME": "Test",
           "GIT_COMMITTER_EMAIL": "t@t.fr", "ELY_PORT": "9"}
    return subprocess.run(["bash", str(mac / "ely.sh"), "update"], env=env, capture_output=True, text=True, timeout=60)


def _published_and_installed(tmp_path: Path) -> tuple[Path, Path]:
    """Un dépôt publié (GitHub) et l'installation d'Ely sur le Mac, dépendances déjà installées."""
    origin, dev, mac = tmp_path / "github.git", tmp_path / "dev", tmp_path / "mac"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(dev))
    (dev / "ely.sh").write_text((ROOT / "ely.sh").read_text())
    (dev / "pyproject.toml").write_text("[project]\nname = 'ely'\n")
    (dev / "notes.txt").write_text("v1\n")
    (dev / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    _git(dev, "add", "-A")
    _git(dev, "commit", "-qm", "v1")
    _git(dev, "remote", "add", "origin", str(origin))
    _git(dev, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(mac))
    (mac / ".venv").mkdir()
    deps = subprocess.run(["sha1sum", "pyproject.toml"], cwd=mac, capture_output=True, text=True).stdout.split()[0]
    (mac / ".venv" / ".deps").write_text(deps + "\n")
    return dev, mac


def test_update_keeps_elys_own_improvements(tmp_path):
    """Ely s'est améliorée sur le Mac (commit local) et une nouvelle version est publiée : un simple `git pull`
    refuse ces historiques divergents ; `./ely.sh update` réunit les deux."""
    dev, mac = _published_and_installed(tmp_path)
    (mac / "amelioration.txt").write_text("fait par Ely\n")
    _git(mac, "add", "-A")
    _git(mac, "commit", "-qm", "Ely s'améliore")
    (dev / "menu.txt").write_text("nouveau menu\n")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-qm", "Nouveau menu")
    _git(dev, "push", "-q")
    r = _update(mac)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "✓ Ely est à jour" in r.stdout
    assert (mac / "amelioration.txt").exists() and (mac / "menu.txt").exists()


def test_update_that_conflicts_leaves_ely_as_it_was(tmp_path):
    dev, mac = _published_and_installed(tmp_path)
    (mac / "notes.txt").write_text("v2 d'Ely\n")
    _git(mac, "commit", "-qam", "Ely modifie les notes")
    before = _git(mac, "rev-parse", "HEAD")
    (dev / "notes.txt").write_text("v2 publiée\n")
    _git(dev, "commit", "-qam", "Notes publiées")
    _git(dev, "push", "-q")
    r = _update(mac)
    assert r.returncode == 1 and "mise à jour impossible" in r.stdout, r.stdout + r.stderr
    assert _git(mac, "rev-parse", "HEAD") == before
    assert _git(mac, "status", "--porcelain") == "" and (mac / "notes.txt").read_text() == "v2 d'Ely\n"
