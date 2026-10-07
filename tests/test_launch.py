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

from ely.__main__ import PORT_BUSY, SHUTDOWN_LIMIT, STARTUP_FAILED, check_port

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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


STUBBORN_TASK = """import asyncio, time
from ely.agent.runner import runner

async def tache_tetue():
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        {on_cancel}

runner.state(999999, 1).task = asyncio.get_running_loop().create_task(tache_tetue())
"""


def _stop_ely_with(tmp_path, on_cancel: str) -> float:
    """Lance Ely avec une tâche qui refuse de s'arrêter, demande l'arrêt (comme le redémarrage après une mise à jour)
    et renvoie le temps qu'il a fallu au processus pour se terminer."""
    import signal

    import httpx

    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "tetue.py").write_text(STUBBORN_TASK.format(on_cancel=on_cancel))
    port = _free_port()
    env = {**os.environ, "ELY_PORT": str(port), "ELY_HOST": "127.0.0.1", "ELY_DATA_DIR": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "ely"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise AssertionError("Ely n'a pas démarré")
        proc.send_signal(signal.SIGTERM)
        started = time.monotonic()
        proc.wait(timeout=SHUTDOWN_LIMIT + 15)
        return time.monotonic() - started
    finally:
        proc.kill()


def test_a_task_that_ignores_cancellation_never_blocks_a_restart(tmp_path):
    """Une tâche qui ne s'arrête pas quand on l'annule (outil mal écrit) retenait le processus : le lanceur attendait
    sans fin et Ely restait hors service après sa mise à jour. Chaque étape de l'arrêt est bornée."""
    assert _stop_ely_with(tmp_path, "await asyncio.sleep(3600)") < 12


def test_even_a_frozen_server_ends_up_stopping(tmp_path):
    """Pire cas : un appel bloquant fige complètement le serveur pendant l'arrêt. Passé SHUTDOWN_LIMIT, il est
    arrêté de force et le lanceur peut le relancer."""
    assert _stop_ely_with(tmp_path, "time.sleep(3600)") < SHUTDOWN_LIMIT + 5


def test_a_failed_startup_is_not_mistaken_for_a_busy_port(tmp_path):
    """Démarrage en échec (ici le dossier des plugins est un fichier) : un code distinct de « port occupé », pour que
    le lanceur relance Ely (et revienne en arrière si une mise à jour en est la cause) au lieu de s'arrêter."""
    (tmp_path / "plugins").write_text("pas un dossier")
    env = {**os.environ, "ELY_PORT": str(_free_port()), "ELY_HOST": "127.0.0.1", "ELY_DATA_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "ely"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == STARTUP_FAILED, r.stderr[-2000:]


FAKE_PYTHON = """#!/bin/bash
# -c sert aussi à normaliser le port ; ce n'est pas un démarrage du serveur.
if [ "${{1:-}}" = "-c" ]; then exec {python} "$@"; fi
# Fausse Ely pour le superviseur : la « bonne » version s'arrête proprement ; la « mauvaise » passe le contrôle de
# santé à son premier démarrage, puis plante à chaque fois.
if grep -q mauvaise version.txt; then
  n=$(cat "$ELY_DATA_DIR/demarrages" 2>/dev/null || echo 0)
  echo $((n + 1)) > "$ELY_DATA_DIR/demarrages"
  if [ "$n" = 0 ]; then
    exec {python} -c '
import http.server, os, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["ELY_PORT"])), H).handle_request()
sys.exit(1)'
  fi
  exit 1
fi
exit 0
"""


def test_repeated_crashes_after_an_update_bring_back_the_previous_version(tmp_path):
    """Une auto-amélioration passe le contrôle de santé puis plante à chaque démarrage : au troisième plantage, le
    lanceur revient à la version précédente au lieu de boucler. Franck avait modifié un fichier à la main : sa
    modification est mise de côté (git stash), jamais écrasée."""
    mac = tmp_path / "mac"
    mac.mkdir()
    _git(mac, "init", "-q", "-b", "main")
    (mac / "ely.sh").write_text((ROOT / "ely.sh").read_text())
    (mac / "pyproject.toml").write_text("[project]\nname = 'ely'\n")
    (mac / ".gitignore").write_text(".venv/\ndata/\n")
    (mac / "version.txt").write_text("bonne\n")
    (mac / "reglages.txt").write_text("v1\n")
    _git(mac, "add", "-A")
    _git(mac, "commit", "-qm", "version saine")
    good = _git(mac, "rev-parse", "HEAD")
    (mac / "version.txt").write_text("mauvaise\n")
    (mac / "reglages.txt").write_text("v2\n")
    _git(mac, "commit", "-qam", "ely-self: amélioration qui plante")
    (mac / "reglages.txt").write_text("réglage fait à la main par Franck\n")
    venv = mac / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text(FAKE_PYTHON.format(python=sys.executable))
    (venv / "python").chmod(0o755)
    deps = subprocess.run(["sha1sum", "pyproject.toml"], cwd=mac, capture_output=True, text=True).stdout.split()[0]
    (mac / ".venv" / ".deps").write_text(deps + "\n")
    data = mac / "data"
    (data / "selfdev").mkdir(parents=True)
    (data / "selfdev" / "pending_check").write_text(good)  # la mise à jour vient d'être déployée

    env = {**os.environ, "ELY_PORT": str(_free_port()), "ELY_DATA_DIR": str(data)}
    r = subprocess.run(["bash", str(mac / "ely.sh")], env=env, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(mac, "rev-parse", "HEAD") == good and (mac / "version.txt").read_text() == "bonne\n"
    assert "plantages répétés" in (data / "selfdev" / "rollback.json").read_text()
    assert (data / "demarrages").read_text().strip() == "3"
    assert "réglage fait à la main par Franck" in _git(mac, "stash", "show", "-p")


def _linux_tools(tmp_path: Path, systemd: bool = True, extra: dict | None = None) -> tuple[dict, Path]:
    """Une session Linux (ou WSL) simulée : uname répond Linux, systemctl note ses appels (systemd absent : refus)."""
    bin_dir, home, log = tmp_path / "bin", tmp_path / "home", tmp_path / "systemctl.log"
    bin_dir.mkdir()
    home.mkdir()
    absent = "" if systemd else '[ "$2" = show-environment ] && exit 1\n'
    tools = {"uname": "#!/bin/sh\necho Linux\n", "systemctl": f'#!/bin/sh\necho "$*" >> "{log}"\n{absent}exit 0\n',
             **(extra or {})}
    for name, body in tools.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "USER": "franck"}, log


def _linux_install(tmp_path: Path, **tools) -> tuple[Path, dict, Path]:
    """Ely installée sous Linux, dépendances à jour."""
    box = tmp_path / "ely"
    venv = box / ".venv" / "bin"
    venv.mkdir(parents=True)
    (box / "ely.sh").write_text((ROOT / "ely.sh").read_text())
    (box / "pyproject.toml").write_text("[project]\nname = 'ely'\n")
    (box / ".env").write_text("")
    (venv / "python").write_text("#!/bin/sh\nexit 0\n")
    (venv / "python").chmod(0o755)
    deps = subprocess.run(["sha1sum", "pyproject.toml"], cwd=box, capture_output=True, text=True).stdout.split()[0]
    (box / ".venv" / ".deps").write_text(deps + "\n")
    env, log = _linux_tools(tmp_path, **tools)
    return box, env, log


def _ely_sh(box: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(box / "ely.sh"), *args], env=env, capture_output=True, text=True, timeout=60)


def test_on_linux_ely_starts_with_the_session_through_systemd(tmp_path):
    """Linux, ou Windows par WSL2 : `./ely.sh service` crée un service systemd de l'utilisateur qui lance le superviseur
    (relancé s'il s'arrête) et le démarre ; `./ely.sh unservice` le retire."""
    box, env, log = _linux_install(tmp_path)
    r = _ely_sh(box, env, "service")
    assert r.returncode == 0, r.stdout + r.stderr
    unit = Path(env["HOME"]) / ".config" / "systemd" / "user" / "ely.service"
    text = unit.read_text()
    assert f'ExecStart="{box}/ely.sh"' in text and f"WorkingDirectory={box}" in text
    assert "Restart=always" in text and "WantedBy=default.target" in text and f"{box}/data/ely.log" in text
    assert "--user enable --now ely.service" in log.read_text()
    assert "loginctl enable-linger franck" in r.stdout
    r = _ely_sh(box, env, "unservice")
    assert r.returncode == 0 and not unit.exists()
    assert "--user disable --now ely.service" in log.read_text()


def test_without_systemd_ely_says_how_to_turn_it_on(tmp_path):
    """WSL sans systemd : pas de service à moitié créé, et la marche à suivre pour l'activer."""
    box, env, log = _linux_install(tmp_path, systemd=False)
    r = _ely_sh(box, env, "service")
    assert r.returncode == 1 and "systemd=true" in r.stdout and "wsl --shutdown" in r.stdout
    assert not (Path(env["HOME"]) / ".config" / "systemd" / "user" / "ely.service").exists()


def test_update_restarts_the_linux_service_on_the_new_version(tmp_path):
    dev, mac = _published_and_installed(tmp_path)
    env, log = _linux_tools(tmp_path)
    unit = Path(env["HOME"]) / ".config" / "systemd" / "user" / "ely.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")
    (dev / "menu.txt").write_text("nouveau menu\n")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-qm", "Nouveau menu")
    _git(dev, "push", "-q")
    env.update(GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@t.fr", GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="t@t.fr", ELY_PORT="9")
    r = _ely_sh(mac, env, "update")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "↻ service Ely redémarré" in r.stdout and "--user restart ely.service" in log.read_text()


def test_install_on_linux_points_out_missing_chromium_libraries(tmp_path):
    """Linux neuf : Playwright télécharge Chromium mais pas les bibliothèques du système (il lui faudrait sudo). Ely
    le signale avec la commande à lancer, au lieu d'un navigateur qui refusera de démarrer plus tard."""
    chrome = tmp_path / "pw" / "chromium-1194" / "chrome-linux" / "chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    missing = "#!/bin/sh\necho '\tlibnss3.so => not found'\n"
    box, env, _ = _linux_install(tmp_path, extra={"uv": "#!/bin/sh\nexit 0\n", "ldd": missing})
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "pw")
    r = _ely_sh(box, env, "install")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "playwright install-deps chromium" in r.stdout and "sudo" in r.stdout
    (tmp_path / "bin" / "ldd").write_text("#!/bin/sh\necho '\tlibnss3.so => /usr/lib/libnss3.so'\n")
    assert "install-deps" not in _ely_sh(box, env, "install").stdout
