"""Catalogue des modèles, choix automatique par rôle et chaîne de repli.

Rôles :
  main   : l'agent (raisonnement + outils)
  strong : escalade quand l'agent piétine (optionnel)
  fast   : contrôles rapides (vérification d'objectif, compaction)
  local  : tâches de fond gratuites (mémoire, titres) — LM Studio de préférence
  embed  : vecteurs pour la mémoire

Les choix de l'administrateur (base de données) priment sur le .env et sont relus
à chaque appel : un réglage enregistré s'applique immédiatement.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

from ..config import settings
from ..db import db, now
from .anthropic_provider import AnthropicProvider
from .chatgpt_provider import ChatGPTProvider
from .base import DeltaCallback, LLMError, LLMResponse, ModelInfo
from .openai_compat import OpenAICompatProvider

log = logging.getLogger("ely.llm")

ROLES = ("main", "strong", "fast", "local", "embed")

# Préférences de choix automatique (motifs appliqués aux modèles découverts)
PREFS: dict[str, list[tuple[str, list[str]]]] = {
    "main": [
        ("anthropic", [r"^claude-opus-5$", r"^claude-opus-4-8$", r"^claude-sonnet-5$"]),
        ("chatgpt", [r"^gpt-5\.\d+$", r"gpt"]),
        ("openai", [r"^gpt-5\.\d+$", r"^gpt-5$", r"^gpt-4\.1$", r"^gpt-4o$"]),
        ("gemini", [r"^gemini-3(\.\d+)?-pro", r"^gemini-2\.5-pro$"]),
        ("openrouter", [r"^anthropic/claude-opus-5$", r"^anthropic/claude-sonnet", r"^openai/gpt-5"]),
        ("deepseek", [r"deepseek-v\d+-pro", r"^deepseek-chat$"]),
        ("mistral", [r"^mistral-large-latest$", r"^mistral-medium-latest$"]),
        ("xai", [r"^grok-4"]), ("moonshot", [r"kimi-k\d"]), ("qwen", [r"qwen3?-max"]), ("zhipu", [r"glm-4\.\d"]),
        ("groq", [r"kimi|gpt-oss-120b|llama-4"]), ("cerebras", [r".*"]), ("together", [r".*"]), ("custom", [r".*"]),
        ("lmstudio", [r"gemma-4-26b", r"qwen3\.5", r"qwen3-30b", r"gpt-oss", r"gemma-4-12b", r"ministral-3-14b"]),
        ("ollama", [r".*"]),
    ],
    "fast": [
        ("anthropic", [r"^claude-haiku-4-5$"]),
        ("openai", [r"^gpt-5(\.\d+)?-mini$", r"^gpt-4\.1-mini$", r"^gpt-4o-mini$"]),
        ("gemini", [r"^gemini-\d(\.\d+)?-flash$", r"flash"]),
        ("deepseek", [r"deepseek-v\d+-flash", r"^deepseek-chat$"]),
        ("mistral", [r"^mistral-small-latest$"]),
        ("groq", [r"gpt-oss|llama"]), ("openrouter", [r"haiku|mini|flash"]),
        ("lmstudio", [r"gemma-4-26b", r"qwen3\.5", r"gemma-4-12b", r"gpt-oss", r"gemma-4-e4b"]),
    ],
    "local": [
        ("lmstudio", [r"gemma-4-26b", r"qwen3\.5", r"gemma-4-12b", r"qwen3", r"gpt-oss", r"gemma-4-e4b", r"ministral"]),
        ("ollama", [r".*"]),
    ],
    "embed": [
        ("lmstudio", [r"nomic-embed", r"embed"]),
        ("ollama", [r"embed"]),
        ("openai", [r"^text-embedding-3-small$"]),
        ("gemini", [r"embedding"]),
        ("mistral", [r"^mistral-embed$"]),
    ],
}
STATIC_FALLBACK = {
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
    "openai": ["gpt-5", "gpt-5-mini", "text-embedding-3-small"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-embedding-001"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "mistral-embed"],
    "deepseek": ["deepseek-chat"],
    "openrouter": ["anthropic/claude-opus-5"],
}
# Sans coût au token : un repli automatique peut y aller même si personne ne les a choisis
FREE_PROVIDERS = ("chatgpt", "lmstudio", "ollama")
# Prix indicatifs ($ / million de tokens entrée, sortie) pour le tableau de bord
PRICES = {
    "claude-fable-5": (10, 50), "claude-opus-5-5": (4, 20), "claude-opus-5": (5, 25), "claude-opus-4": (5, 25),
    "claude-sonnet-5": (2, 10), "claude-sonnet-4": (3, 15), "claude-haiku-4-5": (1, 5),
}


def price_of(model_ref: str) -> tuple[float, float]:
    mid = model_ref.split(":", 1)[-1]
    for prefix, p in PRICES.items():
        if mid.startswith(prefix):
            return p
    return (0.0, 0.0)


class Registry:
    def __init__(self) -> None:
        self.providers: dict[str, AnthropicProvider | OpenAICompatProvider] = {}
        self.catalog: dict[str, list[ModelInfo]] = {}
        self.status: dict[str, str] = {}
        self.refreshed_at = 0.0
        self._lock = asyncio.Lock()
        self.build_providers()

    def build_providers(self) -> None:
        p: dict = {}
        if settings.anthropic_api_key:
            p["anthropic"] = AnthropicProvider(settings.anthropic_api_key)
        if db.get_setting("chatgpt_auth"):  # abonnement ChatGPT importé depuis le CLI Codex
            p["chatgpt"] = ChatGPTProvider()
        for name, (url, key) in settings.openai_compat_keys().items():
            p[name] = OpenAICompatProvider(name, url, key)
        if settings.lmstudio_url:
            p["lmstudio"] = OpenAICompatProvider("lmstudio", settings.lmstudio_url, "lm-studio", timeout=900)
        if settings.ollama_url:
            p["ollama"] = OpenAICompatProvider("ollama", settings.ollama_url, "ollama", timeout=900)
        self.providers = p

    async def refresh(self) -> None:
        """Interroge chaque fournisseur pour connaître ses modèles (en parallèle)."""
        async with self._lock:
            async def one(name, prov):
                try:
                    models = await asyncio.wait_for(prov.list_models(), 12)
                    self.catalog[name] = models
                    self.status[name] = f"ok ({len(models)} modèles)"
                    short = [m.id for m in models if name == "lmstudio" and m.loaded and m.kind == "llm" and m.context < 16000]
                    if short:
                        self.status[name] += (f" · ⚠️ contexte trop court pour {', '.join(short)} : "
                                              "règle au moins 32768 tokens dans LM Studio")
                except Exception as e:  # fournisseur éteint, clé invalide…
                    fallback = [ModelInfo(id=m, provider=name) for m in STATIC_FALLBACK.get(name, [])]
                    self.catalog[name] = fallback
                    prov.models = fallback
                    self.status[name] = f"injoignable : {str(e)[:120]}"
            await asyncio.gather(*(one(n, p) for n, p in self.providers.items()))
            self.refreshed_at = time.time()

    async def ensure_catalog(self) -> None:
        if not self.refreshed_at or time.time() - self.refreshed_at > 1800:
            await self.refresh()

    def reachable(self, name: str) -> bool:
        return self.status.get(name, "").startswith("ok")

    def all_models(self) -> list[dict]:
        out = []
        for name, models in self.catalog.items():
            for m in models:
                out.append({"ref": m.ref, "provider": name, "id": m.id, "kind": m.kind, "vision": m.vision,
                            "loaded": m.loaded, "context": m.context, "reachable": self.reachable(name)})
        return out

    # ------------------------------------------------------------------ choix automatique
    def _pick(self, role: str, exclude_providers: set[str] = frozenset()) -> str | None:
        for prov, patterns in PREFS.get(role, []):
            if prov not in self.providers or prov in exclude_providers:
                continue
            if prov in ("lmstudio", "ollama") and not self.reachable(prov):
                continue
            kind = "embeddings" if role == "embed" else "llm"
            models = [m for m in self.catalog.get(prov, []) if m.kind == kind or (role == "embed" and "embed" in m.id)]
            if prov == "lmstudio" and role != "embed":
                models.sort(key=lambda m: not m.loaded)  # modèles déjà chargés d'abord
            for pat in patterns:
                for m in models:
                    if re.search(pat, m.id, re.I):
                        return m.ref
        return None

    def configured(self, role: str) -> str:
        """Choix explicite (admin > .env), ou '' si automatique."""
        v = (db.get_setting(f"model_{role}") or getattr(settings, f"model_{role}", "auto") or "auto").strip()
        return "" if v in ("auto", "") else v

    def resolve(self, role: str) -> str | None:
        explicit = self.configured(role)
        if explicit:
            return explicit
        if role == "strong":
            return None
        pick = self._pick(role)
        if pick:
            return pick
        if role in ("fast", "local"):
            return self._pick("fast") or self._pick("main")
        return None

    def fallbacks_configured(self) -> str:
        return (db.get_setting("model_fallbacks") or settings.model_fallbacks or "auto").strip() or "auto"

    def chain(self, role: str = "main", preferred: str | None = None) -> list[str]:
        """Modèle du rôle puis replis. En automatique : les modèles choisis pour les autres rôles, puis les
        gratuits (abonnement, local). Jamais un modèle payant que personne n'a choisi."""
        first = preferred or self.resolve(role) or self.resolve("main")
        out: list[str] = [first] if first else []
        fb = self.fallbacks_configured()
        if fb != "auto":
            out += [x.strip() for x in fb.split(",") if x.strip()]
        else:
            out += [ref for r in ("main", "strong", "fast") if (ref := self.resolve(r))]
            used = {r.split(":", 1)[0] for r in out}
            for prov in FREE_PROVIDERS:
                others = set(self.providers) - {prov}
                pick = prov not in used and (self._pick("main", exclude_providers=others) or self._pick("fast", exclude_providers=others))
                if pick:
                    out.append(pick)
        seen, uniq = set(), []
        for r in out:
            if r not in seen:
                seen.add(r)
                uniq.append(r)
        return uniq

    def provider_for(self, ref: str):
        prov, _, model = ref.partition(":")
        if prov not in self.providers:
            raise LLMError(f"fournisseur « {prov} » non configuré", kind="not_found")
        return self.providers[prov], model

    def info(self, ref: str) -> ModelInfo:
        prov, model = self.provider_for(ref)
        return prov.info(model)

    # ------------------------------------------------------------------ appels
    async def chat(self, *, role: str = "main", model: str | None = None, system: list[str], messages: list[dict],
                   tools: list[dict] | None = None, on_delta: DeltaCallback = None, max_tokens: int | None = None,
                   effort: str = "high", user_id: int | None = None, purpose: str = "agent",
                   on_switch: Callable[[str, str], Awaitable[None]] | None = None) -> LLMResponse:
        await self.ensure_catalog()
        chain = self.chain(role, model)
        if not chain:
            raise LLMError("Aucun modèle disponible : configure une clé d'API ou lance LM Studio.", kind="not_found")
        errors = []
        transient = False
        for i, ref in enumerate(chain):
            try:
                prov, mid = self.provider_for(ref)
            except LLMError as e:
                errors.append(str(e))
                continue
            for attempt in range(3):
                try:
                    mt = max_tokens or (32000 if prov.kind == "anthropic" else 16000)
                    resp = await prov.chat(mid, system, messages, tools, on_delta, mt, effort)
                    self.record_usage(user_id, resp, purpose)
                    return resp
                except LLMError as e:
                    log.warning("modèle %s (essai %d) : %s", ref, attempt + 1, e)
                    errors.append(f"{ref}: {e}")
                    if e.kind == "context":
                        raise  # la boucle condense l'historique et réessaie le même modèle, sans en changer
                    transient = transient or e.retryable
                    if e.retryable and attempt < 2:
                        await asyncio.sleep(2 * (3 ** attempt))
                        if on_switch:
                            await on_switch(ref, f"nouvel essai après erreur : {str(e)[:120]}")
                        continue
                    break
            if i + 1 < len(chain) and on_switch:
                await on_switch(chain[i + 1], f"{ref} indisponible, bascule sur {chain[i + 1]}")
        raise LLMError("Aucun modèle n'a pu répondre : " + " | ".join(errors[-4:]), retryable=transient)

    async def complete(self, prompt: str, *, role: str = "fast", system: str = "", max_tokens: int = 4000,
                       user_id: int | None = None, purpose: str = "background") -> str:
        resp = await self.chat(role=role, system=[system] if system else [], max_tokens=max_tokens,
                               messages=[{"role": "user", "content": prompt}], effort="low", user_id=user_id, purpose=purpose)
        return resp.text

    async def embed(self, texts: list[str]) -> tuple[str, list[list[float]]] | None:
        await self.ensure_catalog()
        ref = self.resolve("embed")
        if not ref:
            return None
        try:
            prov, mid = self.provider_for(ref)
            return ref, await prov.embed(mid, texts)
        except Exception as e:
            log.info("embeddings indisponibles (%s) : %s", ref, e)
            return None

    def record_usage(self, user_id: int | None, resp: LLMResponse, purpose: str) -> None:
        try:
            db.insert("usage", user_id=user_id, model=resp.model, input_tokens=resp.input_tokens,
                      output_tokens=resp.output_tokens, cached_tokens=resp.cached_tokens, purpose=purpose, created_at=now())
        except Exception:
            pass

    def roles_view(self) -> dict:
        view = {r: {"configured": self.configured(r) or "auto", "effective": self.resolve(r)} for r in ROLES}
        view["fallbacks"] = {"configured": self.fallbacks_configured(), "effective": ", ".join(self.chain("main")[1:])}
        return view


registry = Registry()
