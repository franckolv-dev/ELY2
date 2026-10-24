"""Adresses qu'un compte ordinaire peut faire joindre à Ely : Internet seulement.

Ely tourne sur le Mac de la maison : sans cette garde, un membre de la famille (ou une page piégée qu'Ely lit pour lui)
pourrait lui faire interroger ce qui n'est joignable que de l'intérieur : la box, le NAS, LM Studio, le service vocal,
l'API d'Ely elle-même, les fichiers du Mac (file:). L'administrateur, lui, garde l'accès à son réseau.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def is_internal(host: str) -> bool:
    """L'hôte désigne-t-il le Mac, le réseau local ou une adresse spéciale ? (un nom inconnu n'est pas interne :
    la connexion échouera d'elle-même)"""
    host = (host or "").strip("[]").lower()
    if not host:
        return True
    try:
        addrs = [ipaddress.ip_address(host.split("%")[0])]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            return False
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    return any(not a.is_global for a in addrs)


class Refused(Exception):
    """Adresse refusée en cours de route (redirection vers le réseau local, par exemple)."""


async def refusal(url: str, is_admin: bool) -> str | None:
    """Raison du refus d'une adresse pour ce compte, ou None si elle est permise."""
    if is_admin:
        return None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.scheme not in ("http", "https"):
        return f"adresse {parts.scheme}: réservée à l'administrateur"
    if await is_internal(parts.hostname or ""):
        return f"{parts.hostname} est une adresse du réseau local, réservée à l'administrateur"
    return None



class GuardProxy:
    """Proxy local par lequel passent les navigateurs des comptes ordinaires. Chaque connexion est vérifiée (page,
    redirection, image, script, cadre, WebSocket) : le Mac et le réseau local sont refusés. Un simple filtre de
    requêtes ne suffit pas : Chromium suit les redirections sans le consulter."""

    def __init__(self) -> None:
        self.server: asyncio.base_events.Server | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def settings(self) -> dict:
        """Réglage proxy de Playwright. « <-loopback> » : Chromium passerait sinon en direct pour localhost."""
        if self.server is None or self.loop is not asyncio.get_running_loop() or not self.server.is_serving():
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            self.loop = asyncio.get_running_loop()
        port = self.server.sockets[0].getsockname()[1]
        return {"server": f"http://127.0.0.1:{port}", "bypass": "<-loopback>"}

    async def close(self) -> None:
        if self.server:
            self.server.close()
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
            line, _, rest = head.partition(b"\r\n")
            method, target, version = line.decode("latin-1").split(" ", 2)
            if method == "CONNECT":  # HTTPS et WebSocket : tunnel vers l'hôte, s'il est permis
                host, _, port = target.rpartition(":")
                host = host.strip("[]")
                if await is_internal(host):
                    return await _refuse(writer)
                up = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 20)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:  # HTTP en clair : une requête par connexion, pour que chaque hôte soit vérifié
                parts = urlsplit(target)
                if parts.scheme != "http" or await is_internal(parts.hostname or ""):
                    return await _refuse(writer)
                up = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port or 80), 20)
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                headers = [h for h in rest.split(b"\r\n") if h and not h.lower().startswith(
                    (b"connection:", b"proxy-connection:", b"keep-alive:"))]
                up[1].write(f"{method} {path} {version}\r\n".encode("latin-1") + b"\r\n".join(headers)
                            + b"\r\nConnection: close\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pipe(reader, up[1]), _pipe(up[0], writer))
        except Exception:  # client parti, hôte injoignable, requête illisible : la connexion est simplement fermée
            pass
        finally:
            for w in (writer, up[1] if up else None):
                if w:
                    w.close()


async def _refuse(writer: asyncio.StreamWriter) -> None:
    body = "Adresse refusée par Ely : le réseau local est réservé à l'administrateur.".encode()
    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n"
                 b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    await writer.drain()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


proxy = GuardProxy()
