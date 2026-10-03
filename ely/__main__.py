"""Lancement : python -m ely"""
from __future__ import annotations

import errno
import logging
import os
import socket
import subprocess
import sys
import threading

PORT_BUSY = 3  # code de sortie : le superviseur (ely.sh) ne relance pas en boucle
STARTUP_FAILED = 4  # démarrage en échec (plugin, base…) : relancé, puis retour arrière s'il suit une mise à jour
SHUTDOWN_LIMIT = 20  # secondes entre l'ordre d'arrêt et l'arrêt forcé (connexions, tâches, navigateur, fils figés)


def port_owner(port: int) -> str:
    """Nom et PID du programme qui écoute sur le port (macOS/Linux, via lsof), ou ''."""
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    pid = name = ""
    for line in out.splitlines():
        if line.startswith("p") and not pid:
            pid = line[1:]
        elif line.startswith("c") and not name:
            name = line[1:]
    return f"{name} (PID {pid})" if name else ""


def running_ely(port: int) -> bool:
    import httpx

    try:
        data = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).json()
        return "tools" in data and "code" in data  # ce qu'Ely renvoie, quelle que soit sa version (l'ancienne : {"status"})
    except Exception:
        return False


def check_port(host: str, port: int) -> str:
    """'' si le port est libre, sinon un message clair expliquant quoi faire."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # comme uvicorn : ignore les connexions en fin de vie
    try:
        s.bind((host, port))
        return ""
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            return ""  # autre souci : laissons uvicorn le signaler
    finally:
        s.close()
    if running_ely(port):
        return (f"Ely tourne déjà sur ce port : ouvre http://localhost:{port}\n"
                "(lancée en service au démarrage du Mac ? « ./ely.sh unservice » pour l'arrêter).")
    owner = port_owner(port)
    return (f"Le port {port} est déjà utilisé{' par ' + owner if owner else ''}.\n"
            f"  • Si c'est l'ancien Ely (Docker) : « docker compose down » dans son dossier.\n"
            f"  • Pour voir qui l'occupe : lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  • Ou choisis un autre port : ELY_PORT=8001 dans le fichier .env")


def main() -> None:
    import uvicorn

    from .config import settings

    problem = check_port(settings.host, settings.port)
    if problem:
        print(f"✗ Ely ne peut pas démarrer. {problem}", file=sys.stderr, flush=True)
        sys.exit(PORT_BUSY)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            # un fil encore occupé (envoi réseau sans délai…) retiendrait le processus indéfiniment, et avec lui le
            # redémarrage après une mise à jour : passé SHUTDOWN_LIMIT, l'arrêt est forcé (les tâches seront reprises)
            if not self.should_exit:
                watchdog = threading.Timer(SHUTDOWN_LIMIT, os._exit, (0,))
                watchdog.daemon = True
                watchdog.start()
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config("ely.api.app:app", host=settings.host, port=settings.port, proxy_headers=True,
                                   forwarded_allow_ips=settings.trusted_proxies, timeout_graceful_shutdown=8,
                                   log_level="info"))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit:  # uvicorn quitte avec 3 si le démarrage échoue : le même code que « port occupé »
        if server.started:
            raise
    if not server.started:
        sys.exit(STARTUP_FAILED)


if __name__ == "__main__":
    main()
