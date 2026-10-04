"""Configuration d'Ely : tout vient du fichier .env (ou de l'environnement).

Un seul endroit, des valeurs par défaut qui marchent : Ely démarre sans rien
configurer d'autre qu'une clé de modèle (ou LM Studio).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
_BOOT_ENV = set(os.environ)  # variables fixées hors .env : elles gardent la priorité
load_dotenv(ROOT / ".env")
_FROM_FILE = set(dotenv_values(ROOT / ".env")) - _BOOT_ENV  # venues du .env : oubliées quand il les retire


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "oui", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


# Fournisseurs compatibles OpenAI : nom -> (variable de clé, URL de base par défaut)
OPENAI_COMPAT_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1"),
    "moonshot": ("MOONSHOT_API_KEY", "https://api.moonshot.ai/v1"),
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    "zhipu": ("ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4"),
    "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1"),
    "together": ("TOGETHER_API_KEY", "https://api.together.xyz/v1"),
}


@dataclass
class Settings:
    host: str = field(default_factory=lambda: _env("ELY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("ELY_PORT", 8000))
    public_url: str = field(default_factory=lambda: _env("ELY_PUBLIC_URL").rstrip("/"))
    # proxys dont l'en-tête X-Forwarded-For est cru (adresse réelle des visiteurs) : jamais « * » (le visiteur l'écrirait)
    trusted_proxies: str = field(default_factory=lambda: _env("ELY_TRUSTED_PROXIES", "127.0.0.1,::1"))
    data_dir: Path = field(default_factory=lambda: Path(_env("ELY_DATA_DIR") or ROOT / "data").resolve())
    timezone: str = field(default_factory=lambda: _env("ELY_TIMEZONE", "Europe/Paris"))
    open_registration: bool = field(default_factory=lambda: _bool("ELY_OPEN_REGISTRATION", False))

    # Modèles : "auto" = choisi automatiquement parmi les fournisseurs configurés
    model_main: str = field(default_factory=lambda: _env("ELY_MODEL_MAIN", "auto"))
    model_fast: str = field(default_factory=lambda: _env("ELY_MODEL_FAST", "auto"))
    model_strong: str = field(default_factory=lambda: _env("ELY_MODEL_STRONG", "auto"))
    model_embed: str = field(default_factory=lambda: _env("ELY_MODEL_EMBED", "auto"))
    model_fallbacks: str = field(default_factory=lambda: _env("ELY_MODEL_FALLBACKS", "auto"))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    # Claude par l'Agent SDK (ely/llm/claude_agent.py) : jeton de `claude setup-token`, sinon la clé d'API
    claude_code_oauth_token: str = field(default_factory=lambda: _env("CLAUDE_CODE_OAUTH_TOKEN"))
    # Compte GitHub d'Ely (jeton classique, droit « repo ») : ses commits et ses PR d'auto-amélioration à son nom
    ely_github_token: str = field(default_factory=lambda: _env("ELY_GITHUB_TOKEN"))
    lmstudio_url: str = field(default_factory=lambda: _env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").rstrip("/"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_BASE_URL").rstrip("/"))
    custom_openai_url: str = field(default_factory=lambda: _env("CUSTOM_OPENAI_BASE_URL").rstrip("/"))
    custom_openai_key: str = field(default_factory=lambda: _env("CUSTOM_OPENAI_API_KEY"))

    # Boucle d'agent
    max_steps: int = field(default_factory=lambda: _int("ELY_MAX_STEPS", 250))
    max_verify_retries: int = field(default_factory=lambda: _int("ELY_MAX_VERIFY_RETRIES", 6))
    context_soft_limit: int = field(default_factory=lambda: _int("ELY_CONTEXT_SOFT_LIMIT", 90_000))

    # Recherche web
    searxng_url: str = field(default_factory=lambda: _env("SEARXNG_URL").rstrip("/"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    brave_api_key: str = field(default_factory=lambda: _env("BRAVE_API_KEY"))

    # Voix enregistrée (voix clonée) : service vocal XTTS qui tourne à part sur le Mac, port 8020 par défaut
    xtts_url: str = field(default_factory=lambda: _env("XTTS_URL", "http://127.0.0.1:8020").rstrip("/"))

    # Navigateur
    browser_headless: bool = field(default_factory=lambda: _bool("ELY_BROWSER_HEADLESS", True))
    browser_channel: str = field(default_factory=lambda: _env("ELY_BROWSER_CHANNEL"))
    browser_executable: str = field(default_factory=lambda: _env("ELY_BROWSER_EXECUTABLE"))

    # Exécution de code
    allow_code: bool = field(default_factory=lambda: _bool("ELY_ALLOW_CODE", True))
    # Python et terminal pour les comptes non administrateurs : ils tournent sur la machine, avec ses droits
    allow_code_for_all: bool = field(default_factory=lambda: _bool("ELY_ALLOW_CODE_FOR_ALL", False))

    # Intégrations
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    linkedin_client_id: str = field(default_factory=lambda: _env("LINKEDIN_CLIENT_ID"))
    linkedin_client_secret: str = field(default_factory=lambda: _env("LINKEDIN_CLIENT_SECRET"))
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    vapid_contact: str = field(default_factory=lambda: _env("ELY_VAPID_CONTACT", "mailto:admin@ely.local"))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "users").mkdir(exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ely.db"

    @property
    def secret_key(self) -> str:
        """Clé secrète persistante, générée au premier lancement."""
        env = _env("ELY_SECRET_KEY")
        if env:
            return env
        path = self.data_dir / "secret.key"
        if not path.exists():
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        return path.read_text().strip()

    def user_dir(self, user_id: int) -> Path:
        p = self.data_dir / "users" / str(user_id)
        (p / "files").mkdir(parents=True, exist_ok=True)
        return p

    def openai_compat_keys(self) -> dict[str, tuple[str, str]]:
        """Fournisseurs compatibles OpenAI réellement configurés : nom -> (url, clé)."""
        out: dict[str, tuple[str, str]] = {}
        for name, (key_var, url) in OPENAI_COMPAT_PROVIDERS.items():
            key = _env(key_var)
            if key:
                out[name] = (_env(f"{name.upper()}_BASE_URL") or url, key)
        if self.custom_openai_url:
            out["custom"] = (self.custom_openai_url, self.custom_openai_key or "none")
        return out

    def external_url(self, request_base: str = "") -> str:
        return self.public_url or request_base.rstrip("/") or f"http://localhost:{self.port}"


settings = Settings()


def reload_env() -> None:
    """Relit le .env sans redémarrer (clés d'API ajoutées, changées, retirées ou commentées, modèles…) et met à jour
    les réglages en place."""
    global _FROM_FILE
    values = {k: v for k, v in dotenv_values(ROOT / ".env").items() if k not in _BOOT_ENV and v is not None}
    for key in _FROM_FILE - set(values):  # retirée ou commentée : Ely ne s'en sert plus
        os.environ.pop(key, None)
    os.environ.update(values)
    _FROM_FILE = set(values)
    fresh = Settings()
    for f in fields(Settings):
        setattr(settings, f.name, getattr(fresh, f.name))
