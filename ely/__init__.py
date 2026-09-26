"""Ely — agent IA personnel autonome."""
import subprocess
from pathlib import Path

__version__ = "2.0.0"


def _code_version() -> str:
    """Commit du code chargé au démarrage (« a22ffa7 (26/09/2026) ») : dit quelle version tourne vraiment."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%d/%m/%Y"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=5).stdout.split()
        return f"{out[0]} ({out[1]})" if len(out) == 2 else __version__
    except (OSError, subprocess.SubprocessError):
        return __version__


CODE_VERSION = _code_version()
