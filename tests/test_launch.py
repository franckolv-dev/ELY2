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
        return {"ok": True, "version": "2.0.0", "tools": 37}

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
