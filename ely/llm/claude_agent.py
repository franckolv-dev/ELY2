"""Claude par l'Agent SDK : Ely confie une mission entière à Claude Code.

Le SDK Python `claude-agent-sdk` (facultatif : `pip install -e ".[claude]"`, installé par `./ely.sh install` quand une
clé est présente) embarque le CLI de Claude Code. Pour chaque mission, Ely lui donne :
- quelques outils natifs (lecture, écriture…), jamais de terminal ; `check` refuse tout ce que la mission n'autorise pas,
  à la fois dans le crochet PreToolUse (appelé pour chaque outil) et dans la demande de permission ;
- ses propres outils par un serveur MCP interne (`mcp__ely__<nom>`), exécutés comme d'habitude par `execute` ;
- un budget en dollars et un nombre de tours maximal.

Identifiants : ANTHROPIC_API_KEY (facturée au token) ou CLAUDE_CODE_OAUTH_TOKEN (jeton créé par `claude setup-token`),
prioritaire si les deux sont présents. Ely ne prend Claude d'elle-même qu'avec ce jeton : avec une clé seule, Claude doit
être choisi dans Réglages → Modèles.
"""
from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from ..config import settings
from ..db import db
from ..tools import Tool, ToolResult

log = logging.getLogger("ely.claude")

PREFIX = "claude:"
MODELS = {"claude:claude-opus-5-5": "Claude Opus 5.5", "claude:claude-fable-5-1": "Claude Fable 5.1"}
DEFAULT = "claude:claude-opus-5-5"
BRIDGE = "ely"  # serveur MCP interne : outils mcp__ely__<nom>
DEFAULT_BUDGET = 5.0  # $ par mission (Réglages → Modèles → Claude)
STOPS = {"error_max_budget_usd": "budget de la mission atteint", "error_max_turns": "nombre maximal de tours atteint",
         "error_during_execution": "erreur pendant l'exécution"}


def sdk_version() -> str:
    try:
        return importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        return ""


def credentials() -> dict[str, str]:
    """Variables passées au CLI : une seule source d'identifiants, l'autre vidée."""
    if settings.claude_code_oauth_token:
        return {"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token, "ANTHROPIC_API_KEY": ""}
    if settings.anthropic_api_key:
        return {"ANTHROPIC_API_KEY": settings.anthropic_api_key, "CLAUDE_CODE_OAUTH_TOKEN": ""}
    return {}


def budget() -> float:
    try:
        return max(0.1, float(db.get_setting("claude_budget", DEFAULT_BUDGET)))
    except (TypeError, ValueError):
        return DEFAULT_BUDGET


def status() -> dict:
    version = sdk_version()
    source = "token" if settings.claude_code_oauth_token else "api_key" if settings.anthropic_api_key else ""
    ready = bool(version and source)
    return {"installed": bool(version), "version": version, "source": source, "ready": ready, "default": DEFAULT,
            "models": [{"ref": ref, "name": name} for ref, name in MODELS.items()] if ready else [], "budget": budget()}


def auto_model() -> str | None:
    """Modèle pris d'office pour l'auto-amélioration : Opus, seulement avec le jeton de Claude Code."""
    return DEFAULT if settings.claude_code_oauth_token and sdk_version() else None


def is_claude(ref: str | None) -> bool:
    return bool(ref) and ref.startswith(PREFIX)


@dataclass
class Session:
    """Une mission confiée à Claude."""
    prompt: str
    cwd: Path
    model: str = DEFAULT
    system: str = ""  # ajouté au prompt système de Claude Code
    tools: list[str] = field(default_factory=list)  # outils natifs proposés
    bridge: list[Tool] = field(default_factory=list)  # outils d'Ely exposés par MCP
    call: Callable[[str, dict], Awaitable[ToolResult]] | None = None  # exécute un outil d'Ely
    permit: Callable[[str, dict], str | None] | None = None  # contrôle des outils natifs : raison du refus ou None
    budget_usd: float = DEFAULT_BUDGET
    max_turns: int = 150
    effort: str = "high"

    @property
    def model_id(self) -> str:
        return self.model.removeprefix(PREFIX)


def check(s: Session, name: str, args: dict) -> str | None:
    """Raison du refus d'un outil demandé par Claude, ou None s'il est permis."""
    prefix = f"mcp__{BRIDGE}__"
    if name.startswith(prefix):
        return None if name[len(prefix):] in {t.name for t in s.bridge} else f"outil {name} non proposé dans cette mission"
    if name not in s.tools:
        return f"outil {name} non autorisé dans cette mission"
    return s.permit(name, args or {}) if s.permit else None


async def run(s: Session) -> AsyncIterator[dict]:
    """Déroule la mission. Événements : text, tool_start, tool_end, puis un seul result
    (ok, text, error, cost, turns, session_id, input_tokens, output_tokens, cached_tokens)."""
    async for ev in _run_sdk(s):
        yield ev


async def ping(model: str = DEFAULT) -> dict:
    """Petit appel sans outil pour vérifier les identifiants."""
    s = Session(prompt="Réponds uniquement : OK", cwd=settings.data_dir, model=model, max_turns=1, budget_usd=0.5, effort="low")
    result: dict = {"ok": False, "error": "aucune réponse"}
    async for ev in run(s):
        if ev["type"] == "result":
            result = ev
    return result


# ---------------------------------------------------------------------- SDK
def bridge_tools(s: Session) -> list:
    """Outils d'Ely au format du serveur MCP interne du SDK."""
    import claude_agent_sdk as sdk

    out = []
    for t in s.bridge:
        async def handler(args: dict, _name: str = t.name) -> dict:
            res = await s.call(_name, args or {})
            return {"content": [{"type": "text", "text": res.content or "(vide)"}], "is_error": res.is_error}

        out.append(sdk.tool(t.name, t.description, t.parameters)(handler))
    return out


def options(s: Session):
    import claude_agent_sdk as sdk

    async def guard(data: dict, tool_use_id: str | None, context: Any) -> dict:
        why = check(s, data.get("tool_name", ""), data.get("tool_input") or {})
        if why:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": why}}
        return {}

    async def can_use(name: str, args: dict, context: Any):
        why = check(s, name, args)
        return sdk.PermissionResultDeny(message=why) if why else sdk.PermissionResultAllow()

    prompt: dict = {"type": "preset", "preset": "claude_code"}
    if s.system:
        prompt["append"] = s.system
    return sdk.ClaudeAgentOptions(
        model=s.model_id, cwd=str(s.cwd), tools=list(s.tools), system_prompt=prompt,
        allowed_tools=[f"mcp__{BRIDGE}__{t.name}" for t in s.bridge],
        mcp_servers={BRIDGE: sdk.create_sdk_mcp_server(BRIDGE, tools=bridge_tools(s))} if s.bridge else {},
        strict_mcp_config=True, setting_sources=["project"],  # CLAUDE.md du projet, pas les réglages de la machine
        can_use_tool=can_use, hooks={"PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[guard])]},
        max_budget_usd=s.budget_usd, max_turns=s.max_turns, effort=s.effort,
        env={**credentials(), "MCP_TOOL_TIMEOUT": str(25 * 60 * 1000)},  # tests et déploiement peuvent durer
    )


def _text(content) -> str:
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


async def _run_sdk(s: Session) -> AsyncIterator[dict]:
    import claude_agent_sdk as sdk

    names: dict[str, str] = {}
    failure = ""
    async for m in sdk.query(prompt=s.prompt, options=options(s)):
        if isinstance(m, sdk.AssistantMessage):
            failure = m.error or failure
            for b in m.content:
                if isinstance(b, sdk.TextBlock) and b.text.strip():
                    yield {"type": "text", "text": b.text}
                elif isinstance(b, sdk.ToolUseBlock):
                    names[b.id] = b.name
                    yield {"type": "tool_start", "id": b.id, "name": b.name, "input": b.input}
        elif isinstance(m, sdk.UserMessage) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, sdk.ToolResultBlock):
                    yield {"type": "tool_end", "id": b.tool_use_id, "name": names.get(b.tool_use_id, ""),
                           "ok": not b.is_error, "content": _text(b.content)}
        elif isinstance(m, sdk.ResultMessage):
            u = m.usage or {}
            cached = u.get("cache_read_input_tokens") or 0
            error = ""
            if m.is_error:
                # erreur d'API (quota du forfait, 429, identifiants…) : le message du CLI, plus parlant que son code
                error = ("; ".join(m.errors or []) or STOPS.get(m.subtype) or (m.result or "").strip()[:300]
                         or failure or m.subtype)
            yield {"type": "result", "ok": not m.is_error, "text": m.result or "", "error": error,
                   "cost": m.total_cost_usd or 0.0, "turns": m.num_turns, "session_id": m.session_id,
                   "input_tokens": (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0),
                   "output_tokens": u.get("output_tokens") or 0, "cached_tokens": cached}
