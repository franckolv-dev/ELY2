"""Types communs à tous les fournisseurs de modèles.

Format canonique des messages (proche d'OpenAI, converti par chaque adaptateur) :
  {"role": "user", "content": str | [{"type": "text", "text": ...}, {"type": "image", "media_type": ..., "data": b64}]}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict, "extra"?}],
   "thinking": [blocs bruts Anthropic], "model": "fournisseur:modèle"}
  {"role": "tool", "tool_call_id": ..., "name": ..., "content": str, "images": [{"media_type", "data"}], "is_error": bool}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

DeltaCallback = Optional[Callable[[str, str], Awaitable[None]]]  # (kind: "text"|"thinking", texte)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    extra: dict | None = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "arguments": self.arguments}
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def to_message(self) -> dict:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text, "model": self.model}
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.thinking:
            msg["thinking"] = self.thinking
        return msg


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int = 0, kind: str = "error", model: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.kind = kind  # error | refusal | context | auth | not_found | timeout
        self.model = model  # modèle en cause (contexte trop long : celui pour lequel condenser l'historique)


@dataclass
class ModelInfo:
    id: str
    provider: str
    vision: bool = True
    context: int = 128_000
    loaded: bool = True
    kind: str = "llm"  # llm | embeddings

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.id}"


def parse_json_loose(text: str) -> Any:
    """Extrait un objet JSON d'une réponse de modèle (tolère le texte autour et ```json)."""
    if not text:
        raise ValueError("réponse vide")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("aucun JSON trouvé")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON incomplet")


def parse_tool_arguments(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw or not raw.strip():
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"_invalid": raw}
    except json.JSONDecodeError:
        try:
            v = parse_json_loose(raw)
            return v if isinstance(v, dict) else {"_invalid": raw}
        except Exception:
            return {"_invalid": raw}


def message_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += len(message_text(m)) // 3
        # Ces blocs sont rejoués par les adaptateurs (Responses/Anthropic), même
        # quand le texte visible est vide. Le chiffrement interdit un comptage
        # exact : conserver ici la même heuristique prudente en caractères/3.
        if m.get("thinking"):
            total += len(json.dumps(m["thinking"], ensure_ascii=False)) // 3
        for tc in m.get("tool_calls") or []:
            total += len(json.dumps(tc.get("arguments", {}), ensure_ascii=False)) // 3
        total += 1200 * len(m.get("images") or [])
        c = m.get("content")
        if isinstance(c, list):
            total += 1200 * sum(1 for p in c if p.get("type") == "image")
    return total
