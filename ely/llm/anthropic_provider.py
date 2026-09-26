"""Adaptateur Claude via le SDK officiel Anthropic (API Messages native).

- cache de prompt automatique (cache_control au niveau de la requête)
- réflexion adaptative + effort sur les modèles récents
- repli côté serveur en cas de refus (fallbacks: "default") sur Opus 5 / Fable
- blocs de réflexion renvoyés tels quels dans la boucle d'outils
"""
from __future__ import annotations

import re

import anthropic

from .base import DeltaCallback, LLMError, LLMResponse, ModelInfo, ToolCall, parse_tool_arguments

ADAPTIVE = re.compile(r"claude-(opus-(4-[6-9]|5)|sonnet-(4-6|5)|fable|mythos)")
EFFORT = re.compile(r"claude-(opus-(4-[5-9]|5)|sonnet-5|fable|mythos)")
SERVER_FALLBACK = re.compile(r"claude-(opus-5(?!-5)|fable-5-1|fable-5$)")
DEFAULT_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5-5", "claude-fable-5-1"]


class AnthropicProvider:
    kind = "anthropic"
    name = "anthropic"

    # options récentes de l'API, désactivées d'elles-mêmes si le compte ou le modèle les refuse
    OPTIONAL = {"fallbacks": ("fallback", "beta"), "eager": ("eager_input_streaming",), "display": ("display",),
                "effort": ("effort", "output_config"), "thinking": ("thinking", "adaptive")}

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None, max_retries=2, timeout=900)
        self.models: list[ModelInfo] = []
        self.disabled: set[str] = set()

    async def list_models(self) -> list[ModelInfo]:
        out = []
        try:
            page = await self.client.models.list(limit=100)
            for m in page.data:
                ctx = getattr(m, "max_input_tokens", None) or 200_000
                out.append(ModelInfo(id=m.id, provider="anthropic", vision=True, context=int(ctx)))
        except anthropic.APIError:
            out = [ModelInfo(id=mid, provider="anthropic", context=1_000_000) for mid in DEFAULT_MODELS]
        self.models = out
        return out

    def info(self, model: str) -> ModelInfo:
        for m in self.models:
            if m.id == model:
                return m
        return ModelInfo(id=model, provider="anthropic", context=200_000 if "haiku" in model else 1_000_000)

    # ------------------------------------------------------------------ conversion
    @staticmethod
    def convert_messages(messages: list[dict], model_ref: str, keep_thinking: bool = True) -> list[dict]:
        out: list[dict] = []

        def push(role: str, blocks: list[dict]) -> None:
            if not blocks:
                return
            if out and out[-1]["role"] == role:
                if role == "user":
                    # les tool_result doivent précéder le texte dans un message utilisateur
                    merged = out[-1]["content"] + blocks
                    out[-1]["content"] = [b for b in merged if b["type"] == "tool_result"] + \
                                         [b for b in merged if b["type"] != "tool_result"]
                else:
                    out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": list(blocks)})

        for m in messages:
            role = m["role"]
            if role == "user":
                c = m["content"]
                if isinstance(c, str):
                    blocks = [{"type": "text", "text": c or "…"}]
                else:
                    blocks = []
                    for p in c:
                        if p["type"] == "text" and p["text"]:
                            blocks.append({"type": "text", "text": p["text"]})
                        elif p["type"] == "image":
                            blocks.append({"type": "image", "source": {"type": "base64", "media_type": p["media_type"], "data": p["data"]}})
                push("user", blocks)
            elif role == "assistant":
                blocks = []
                if keep_thinking and m.get("thinking") and m.get("model") == model_ref:
                    blocks.extend(m["thinking"])
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments") or {}})
                if not blocks:
                    blocks = [{"type": "text", "text": "…"}]
                push("assistant", blocks)
            elif role == "tool":
                content: list[dict] = [{"type": "text", "text": m.get("content") or "(vide)"}]
                for img in m.get("images") or []:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]}})
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": content}
                if m.get("is_error"):
                    block["is_error"] = True
                push("user", [block])
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(suite de la conversation)"}]})
        if out and out[-1]["role"] == "assistant":
            out.append({"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
        return out

    @staticmethod
    def convert_tools(tools: list[dict]) -> list[dict]:
        return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"],
                 "eager_input_streaming": True} for t in tools]

    # ------------------------------------------------------------------ appel
    async def chat(self, model: str, system: list[str], messages: list[dict], tools: list[dict] | None = None,
                   on_delta: DeltaCallback = None, max_tokens: int = 32000, effort: str = "high") -> LLMResponse:
        ref = f"anthropic:{model}"
        keep_thinking = True
        for _ in range(5):
            try:
                return await self._chat(model, ref, system, messages, tools, on_delta, max_tokens, effort, keep_thinking)
            except LLMError as e:
                if e.status != 400:
                    raise
                msg = str(e).lower()
                if keep_thinking and "thinking" in msg and "block" in msg:
                    keep_thinking = False  # historique retouché (compaction) : sans les anciens blocs de réflexion
                    continue
                culprit = next((k for k, words in self.OPTIONAL.items() if k not in self.disabled and any(w in msg for w in words)), None)
                if not culprit:
                    raise
                self.disabled.add(culprit)
        raise LLMError("anthropic : requête refusée malgré les ajustements", status=400)

    async def _chat(self, model, ref, system, messages, tools, on_delta, max_tokens, effort, keep_thinking) -> LLMResponse:
        params: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": s} for s in system if s],
            "messages": self.convert_messages(messages, ref, keep_thinking),
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = self.convert_tools(tools)
            if "eager" in self.disabled:
                for t in params["tools"]:
                    t.pop("eager_input_streaming", None)
        if ADAPTIVE.search(model) and "thinking" not in self.disabled:
            params["thinking"] = {"type": "adaptive"} if "display" in self.disabled else {"type": "adaptive", "display": "summarized"}
        if EFFORT.search(model) and "effort" not in self.disabled:
            params["output_config"] = {"effort": effort}
        use_beta = bool(SERVER_FALLBACK.search(model)) and "fallbacks" not in self.disabled
        if use_beta:
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"

        stream_api = self.client.beta.messages.stream if use_beta else self.client.messages.stream
        try:
            async with stream_api(**params) as stream:
                async for event in stream:
                    if not on_delta:
                        continue
                    if event.type == "text":
                        await on_delta("text", event.text)
                    elif event.type == "thinking":
                        await on_delta("thinking", event.thinking)
                final = await stream.get_final_message()
        except ValueError as e:  # JSON d'outil illisible (streaming anticipé des arguments)
            raise LLMError(f"anthropic: arguments d'outil illisibles ({e})", retryable=True) from e
        except anthropic.AuthenticationError as e:
            raise LLMError(f"anthropic: clé refusée ({e.message})", status=401, kind="auth") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"anthropic: modèle introuvable ({e.message})", status=404, kind="not_found") from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"anthropic: limite de débit ({e.message})", status=429, retryable=True) from e
        except anthropic.BadRequestError as e:
            kind = "context" if "too long" in e.message.lower() or "context" in e.message.lower() else "error"
            raise LLMError(f"anthropic: requête refusée ({e.message})", status=400, kind=kind) from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"anthropic: HTTP {e.status_code} ({e.message})", status=e.status_code,
                           retryable=e.status_code >= 500 or e.status_code in (408, 409, 429)) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"anthropic: erreur réseau ({e})", retryable=True) from e

        if final.stop_reason == "refusal":
            raise LLMError("anthropic: le modèle a refusé la demande", kind="refusal")

        text_parts, calls, thinking = [], [], []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=parse_tool_arguments(block.input)))
            elif block.type in ("thinking", "redacted_thinking"):
                thinking.append(block.model_dump(exclude_none=True))
        if final.stop_reason == "max_tokens" and calls:
            raise LLMError("anthropic: réponse tronquée pendant un appel d'outil", retryable=True)
        u = final.usage
        return LLMResponse(
            text="".join(text_parts).strip(), tool_calls=calls, thinking=thinking, stop_reason=final.stop_reason or "",
            model=ref,
            input_tokens=(u.input_tokens or 0) + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0),
            output_tokens=u.output_tokens or 0, cached_tokens=u.cache_read_input_tokens or 0,
        )

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise LLMError("anthropic: pas d'API d'embeddings", kind="not_found")
