"""Ely — agent IA personnel autonome."""
import subprocess
from pathlib import Path

__version__ = "4.0.0"  # succède à la 3.1.0 d'ElyAgent (réécriture complète)


def _code_version() -> str:
    """Version et commit du code chargé au démarrage (« 4.0.0 · a22ffa7 (26/09/2026) ») : dit ce qui tourne vraiment."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%d/%m/%Y"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=5).stdout.split()
        return f"{__version__} · {out[0]} ({out[1]})" if len(out) == 2 else __version__
    except (OSError, subprocess.SubprocessError):
        return __version__


CODE_VERSION = _code_version()
