"""Lancement : python -m ely"""
from __future__ import annotations

import logging


def main() -> None:
    import uvicorn

    from .config import settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run("ely.api.app:app", host=settings.host, port=settings.port, proxy_headers=True, forwarded_allow_ips="*",
                timeout_graceful_shutdown=8, log_level="info")


if __name__ == "__main__":
    main()
