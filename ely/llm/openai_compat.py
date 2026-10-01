"""Adaptateur pour toutes les API compatibles OpenAI.

Couvre LM Studio, Ollama, OpenAI, Gemini, Mistral, DeepSeek, OpenRouter, Groq,
xAI, Moonshot, Qwen, Zhipu, Cerebras, Together et tout point d'accès personnalisé.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

import httpx

from .base import DeltaCallback, LLMError, LLMResponse, ModelInfo, ToolCall, parse_tool_arguments

VISION_HINTS = re.compile(r"(gpt-4o|gpt-4\.1|gpt-5|o3|o4|gemini|claude|vision|vl\b|-vl-|pixtral|llava|gemma-3|gemma-4|qwen2\.5-vl|qwen3-vl|grok-4|mistral-medium|mistral-small|kimi-k2|glm-4\.\dv|llama-4)", re.I)
NO_STREAM_OPTIONS = {"mistral", "gemini", "zhipu", "moonshot"}


def _mistral_id(tid: str) -> str:
    return hashlib.sha1(tid.encode()).hexdigest()[:9]


class ThinkFilter:
    """Sépare les balises <think>…</think> (Qwen, DeepSeek…) du texte visible, en streaming."""

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        self.buf += chunk
        out: list[tuple[str, str]] = []
        while self.buf:
            tag = "</think>" if self.inside else "<think>"
            i = self.buf.find(tag)
            if i >= 0:
                if i:
                    out.append(("thinking" if self.inside else "text", self.buf[:i]))
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            # garder en réserve un éventuel début de balise coupé
            keep = 0
            for k in range(1, len(tag)):
                if self.buf.endswith(tag[:k]):
                    keep = k
            emit = self.buf[: len(self.buf) - keep]
            if emit:
                out.append(("thinking" if self.inside else "text", emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buf = self.buf, ""
        return [("thinking" if self.inside else "text", rest)] if rest else []


class OpenAICompatProvider:
    kind = "openai"

    def __init__(self, name: str, base_url: str, api_key: str = "", timeout: float = 600) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.models: list[ModelInfo] = []

    @property
    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter":
            h["HTTP-Referer"] = "https://github.com/franckolv-dev/ELY2"
            h["X-Title"] = "Ely"
        return h

    # ------------------------------------------------------------------ modèles
    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=8) as c:
            if self.name == "lmstudio":
                root = self.base_url.removesuffix("/v1")
                try:
                    r = await c.get(f"{root}/api/v0/models")
                    if r.status_code == 200:
                        out = []
                        for m in r.json().get("data", []):
                            kind = "embeddings" if m.get("type") == "embeddings" else "llm"
                            out.append(ModelInfo(
                                id=m["id"], provider=self.name, kind=kind,
                                vision=m.get("type") == "vlm" or bool(VISION_HINTS.search(m["id"])),
                                context=int(m.get("loaded_context_length") or m.get("max_context_length") or 32_000),
                                loaded=m.get("state") == "loaded",
                            ))
                        self.models = out
                        return out
                except httpx.HTTPError:
                    pass
            r = await c.get(f"{self.base_url}/models", headers=self.headers)
            r.raise_for_status()
            data = r.json()
            items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
            out = []
            for m in items:
                mid = m.get("id") or m.get("name", "")
                if self.name == "gemini":
                    mid = mid.removeprefix("models/")
                if not mid:
                    continue
                kind = "embeddings" if "embed" in mid.lower() else "llm"
                ctx = int(m.get("context_length") or m.get("context_window") or m.get("max_context_length") or 128_000)
                out.append(ModelInfo(id=mid, provider=self.name, kind=kind, vision=bool(VISION_HINTS.search(mid)),
                                     context=ctx))
            self.models = out
            return out

    def info(self, model: str) -> ModelInfo:
        for m in self.models:
            if m.id == model:
                return m
        ctx = 32_000 if self.name in ("lmstudio", "ollama") else 128_000
        return ModelInfo(id=model, provider=self.name, vision=bool(VISION_HINTS.search(model)), context=ctx)

    # ------------------------------------------------------------------ conversion
    def _tid(self, tid: str) -> str:
        return _mistral_id(tid) if self.name == "mistral" else tid

    def convert_messages(self, system: list[str], messages: list[dict], vision: bool) -> list[dict]:
        out: list[dict] = []
        sys_text = "\n\n".join(s for s in system if s)
        if sys_text:
            out.append({"role": "system", "content": sys_text})
        pending_images: list[dict] = []

        def flush_images() -> None:
            if pending_images:
                parts = [{"type": "text", "text": "[Images renvoyées par les outils ci-dessus]"}]
                for img in pending_images:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}})
                out.append({"role": "user", "content": parts})
                pending_images.clear()

        for m in messages:
            role = m["role"]
            if role != "tool":
                flush_images()
            if role == "user":
                c = m["content"]
                if isinstance(c, list):
                    parts = []
                    for p in c:
                        if p["type"] == "text":
                            parts.append({"type": "text", "text": p["text"]})
                        elif p["type"] == "image":
                            if vision:
                                parts.append({"type": "image_url", "image_url": {"url": f"data:{p['media_type']};base64,{p['data']}"}})
                            else:
                                parts.append({"type": "text", "text": "[image jointe non visible par ce modèle]"})
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": c})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    calls = []
                    for tc in m["tool_calls"]:
                        call = {"id": self._tid(tc["id"]), "type": "function",
                                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False)}}
                        if tc.get("extra") and self.name == "gemini":
                            call.update(tc["extra"])
                        calls.append(call)
                    msg["tool_calls"] = calls
                elif not msg["content"]:
                    msg["content"] = "…"
                out.append(msg)
            elif role == "tool":
                content = m.get("content") or ""
                out.append({"role": "tool", "tool_call_id": self._tid(m["tool_call_id"]), "content": content})
                if m.get("images"):
                    if vision:
                        pending_images.extend(m["images"])
        flush_images()
        return out

    @staticmethod
    def convert_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}} for t in tools]

    # ------------------------------------------------------------------ appel
    async def chat(self, model: str, system: list[str], messages: list[dict], tools: list[dict] | None = None,
                   on_delta: DeltaCallback = None, max_tokens: int = 16000, effort: str = "high") -> LLMResponse:
        info = self.info(model)
        body: dict = {"model": model, "messages": self.convert_messages(system, messages, info.vision), "stream": True}
        if tools:
            body["tools"] = self.convert_tools(tools)
        if self.name == "openai":
            body["max_completion_tokens"] = max_tokens
        else:  # modèles locaux compris : un modèle qui boucle s'arrête au lieu de générer sans fin
            body["max_tokens"] = min(max_tokens, 8192 if self.name in ("deepseek", "groq") else max_tokens)
        if self.name not in NO_STREAM_OPTIONS:
            body["stream_options"] = {"include_usage": True}
        if (self.name == "openai" and re.match(r"(gpt-5|o\d)", model)) or (self.name == "gemini" and re.search(r"gemini-(2\.5|[3-9])", model)):
            body["reasoning_effort"] = effort if effort in ("low", "medium", "high") else "medium"

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish = ""
        usage: dict = {}
        think = ThinkFilter()

        async def emit(kind: str, chunk: str) -> None:
            if kind == "text":
                text_parts.append(chunk)
            if on_delta and chunk:
                await on_delta(kind, chunk)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=15)) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers, json=body) as r:
                    if r.status_code >= 400:
                        detail = (await r.aread()).decode(errors="replace")[:800]
                        raise self._http_error(r.status_code, detail)
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(str(chunk["error"])[:500], retryable=True)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if isinstance(reasoning, str) and reasoning and on_delta:
                                await on_delta("thinking", reasoning)
                            if delta.get("content"):
                                for kind, part in think.feed(delta["content"]):
                                    await emit(kind, part)
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", len(calls))
                                slot = calls.setdefault(idx, {"id": "", "name": "", "args": "", "extra": None})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["args"] += fn["arguments"] if isinstance(fn["arguments"], str) else json.dumps(fn["arguments"])
                                if tc.get("extra_content"):
                                    slot["extra"] = {"extra_content": tc["extra_content"]}
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
        except httpx.TimeoutException as e:
            raise LLMError(f"{self.name}: délai dépassé ({e.__class__.__name__})", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name}: erreur réseau {e.__class__.__name__}: {e}", retryable=True) from e

        for kind, part in think.flush():
            await emit(kind, part)

        tool_calls = []
        for idx in sorted(calls):
            slot = calls[idx]
            if not slot["name"]:
                continue
            tool_calls.append(ToolCall(id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}", name=slot["name"].strip(),
                                       arguments=parse_tool_arguments(slot["args"]), extra=slot["extra"]))
        details = usage.get("prompt_tokens_details") or {}
        return LLMResponse(
            text="".join(text_parts).strip(), tool_calls=tool_calls, stop_reason=finish or "stop",
            model=f"{self.name}:{model}", input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0), cached_tokens=int(details.get("cached_tokens") or 0),
        )

    def _http_error(self, status: int, detail: str) -> LLMError:
        low = detail.lower()
        if status in (401, 403):
            return LLMError(f"{self.name}: clé refusée ({status}) {detail[:200]}", status=status, kind="auth")
        if status == 404:
            return LLMError(f"{self.name}: modèle introuvable ({detail[:200]})", status=status, kind="not_found")
        if status == 400 and any(k in low for k in ("context length", "context_length", "maximum context", "too many tokens", "too long")):
            return LLMError(f"{self.name}: contexte trop long", status=status, kind="context")
        retry = status in (408, 409, 425, 429) or status >= 500
        return LLMError(f"{self.name}: HTTP {status} {detail[:300]}", retryable=retry, status=status)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}/embeddings", headers=self.headers, json={"model": model, "input": texts})
            if r.status_code >= 400:
                raise self._http_error(r.status_code, r.text[:500])
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]
