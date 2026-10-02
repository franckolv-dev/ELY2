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
