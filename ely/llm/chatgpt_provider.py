"""Abonnement ChatGPT (forfait) : GPT sans facturation au token, via le backend de Codex.

Même mécanisme que la version précédente d'Ely (et que Hermes, ou le CLI Codex lui-même) :
on se connecte une fois avec le CLI officiel (`codex login`), on importe ~/.codex/auth.json,
puis Ely rafraîchit les jetons toute seule (la rotation du jeton de rafraîchissement est conservée).

Codex et Ely partagent la même session (celle de ~/.codex/auth.json) et OpenAI remplace la clé de
renouvellement à chaque renouvellement : l'ancienne ne vaut plus rien. Comme Codex entre ses propres
processus, Ely relit donc ce fichier (Codex a-t-il renouvelé la session ?) et y réécrit la session
qu'elle renouvelle. Sans cela, le premier renouvellement de Codex coupait Ely de l'abonnement.

Contraintes du backend, validées en réel dans la version précédente : stream et store=false
obligatoires, `instructions` obligatoire, max_output_tokens refusé, raisonnement renvoyé chiffré
(include=reasoning.encrypted_content) pour pouvoir le rejouer sans stockage côté serveur.
Mécanisme non officiel : il peut changer, et l'usage compte dans les limites du forfait ChatGPT.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from ..db import db
from .base import DeltaCallback, LLMError, LLMResponse, ModelInfo, ToolCall, parse_tool_arguments

BASE_URL = os.environ.get("CHATGPT_BASE_URL", "https://chatgpt.com/backend-api/codex")
TOKEN_URL = os.environ.get("CHATGPT_TOKEN_URL", "https://auth.openai.com/oauth/token")
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
SETTING = "chatgpt_auth"
INSTRUCTIONS = "Suis les messages système fournis dans la conversation."
_lock = asyncio.Lock()
log = logging.getLogger("ely.llm")


# ---------------------------------------------------------------------- jetons
def parse_auth_json(raw: str | dict) -> dict:
    """Accepte le ~/.codex/auth.json du CLI (clé « tokens ») ou un dict plat."""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:  # collage depuis un terminal : sauts de ligne au milieu des jetons
            data = json.loads(raw.replace("\n", "").replace("\r", ""))
    else:
        data = dict(raw)
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
    refresh = (tokens.get("refresh_token") or "").strip()
    if not refresh:
        raise LLMError("auth.json invalide : refresh_token absent. Lance « codex login » puis réimporte.", kind="auth")
    return {"access_token": (tokens.get("access_token") or "").strip(), "refresh_token": refresh,
            "account_id": (tokens.get("account_id") or "").strip(), "expires_at": 0.0}


def stored() -> dict | None:
    return db.get_setting(SETTING)


def status() -> dict:
    st = stored()
    if st and st.get("reconnect_required"):
        st = _adopt_codex(st, spent=True) or st  # Codex s'est reconnecté depuis : Ely reprend sa session
    base = {"codex_file": codex_file_exists(), "codex_model": codex_config().get("model", ""),
            "keyring": codex_config().get("cli_auth_credentials_store", "") in ("keyring", "auto")}
    if not st:
        return {"connected": False, **base}
    return {"connected": not st.get("reconnect_required"), "reconnect_required": bool(st.get("reconnect_required")),
            "account_id": st.get("account_id", ""), **base}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_file() -> Path:
    return codex_home() / "auth.json"


def codex_file_exists() -> bool:
    return codex_file().exists()


def codex_config() -> dict:
    """~/.codex/config.toml : modèle utilisé dans Codex, mode de stockage des identifiants."""
    import tomllib

    try:
        return tomllib.loads((codex_home() / "config.toml").read_text())
    except (OSError, ValueError):
        return {}


def model_names() -> list[str]:
    """Modèles proposés : CHATGPT_MODELS, puis celui configuré dans Codex, puis le modèle par défaut."""
    names = [m.strip() for m in os.environ.get("CHATGPT_MODELS", "").split(",") if m.strip()]
    configured = str(codex_config().get("model") or "").strip()
    for m in [configured, "gpt-5.5"]:
        if m and m not in names:
            names.append(m)
    return names


# ---------------------------------------------------------------------- session partagée avec Codex
def _jwt_exp(token: str) -> float:
    """Échéance d'un jeton JWT (0 si illisible : il sera renouvelé)."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp") or 0)
    except (IndexError, ValueError, TypeError, AttributeError):
        return 0.0


def _timestamp(value) -> float:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def codex_session() -> dict | None:
    """La session ChatGPT de Codex (~/.codex/auth.json), au format d'Ely ; None si le fichier manque."""
    try:
        data = json.loads(codex_file().read_text())
        tokens = data["tokens"]
        refresh = tokens["refresh_token"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    access = tokens.get("access_token") or ""
    return {"access_token": access, "refresh_token": refresh, "account_id": tokens.get("account_id") or "",
            "expires_at": _jwt_exp(access), "refreshed_at": _timestamp(data.get("last_refresh"))}


def _adopt_codex(state: dict, spent: bool = False) -> dict | None:
    """Codex a renouvelé la session partagée (ou vous l'avez reconnecté) : Ely reprend ses jetons.
    `spent` : la clé d'Ely vient d'être refusée, celle de Codex est la seule chance."""
    f = codex_session()
    if not f or f["refresh_token"] == state.get("refresh_token"):
        return None
    if f["account_id"] and state.get("account_id") and f["account_id"] != state["account_id"]:
        return None  # Codex est connecté à un autre compte ChatGPT
    if not spent and f["refreshed_at"] <= state.get("refreshed_at", 0):
        return None  # la session d'Ely est la plus récente (jetons collés à la main)
    new = {**f, "account_id": f["account_id"] or state.get("account_id", "")}
    db.set_setting(SETTING, new)
    log.info("chatgpt : session renouvelée par Codex reprise depuis %s", codex_file())
    return new


def _give_back_to_codex(spent_refresh: str, new: dict, id_token: str) -> None:
    """Ely vient de renouveler la session de Codex : la clé que garde Codex ne vaut plus rien, on lui rend
    la nouvelle. Seulement si le fichier porte bien la clé qu'Ely vient d'user (c'est la même session)."""
    path = codex_file()
    try:
        data = json.loads(path.read_text())
        tokens = data["tokens"]
        if tokens.get("refresh_token") != spent_refresh:
            return
        tokens["access_token"], tokens["refresh_token"] = new["access_token"], new["refresh_token"]
        if id_token:
            tokens["id_token"] = id_token
        data["last_refresh"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        tmp = path.with_name(f"{path.name}.ely")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("chatgpt : session renouvelée non rendue à Codex (%s) : %s", path, e)


async def _refresh(state: dict, force: bool = False) -> dict:
    if not force and state.get("expires_at", 0) - time.time() > 120 and state.get("access_token"):
        return state
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, json={"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                          "refresh_token": state["refresh_token"]})
    if r.status_code != 200:
        adopted = _adopt_codex(state, spent=True)  # Codex a usé la clé partagée avant Ely : sa session prend le relais
        if adopted:
            return await _refresh(adopted)
        if stored():
            db.set_setting(SETTING, {**state, "reconnect_required": True})
        hint = ("reconnectez Codex (« codex login ») : Ely reprendra sa session d'elle-même" if codex_file_exists()
                else "relancez « codex login » puis réimportez dans Réglages → Modèles")
        raise LLMError(f"chatgpt : connexion à l'abonnement expirée ({r.status_code}) — {hint}", kind="auth", status=r.status_code)
    p = r.json()
    new = {"access_token": p.get("access_token", ""), "refresh_token": p.get("refresh_token") or state["refresh_token"],
           "account_id": p.get("account_id") or state.get("account_id", ""),
           "expires_at": time.time() + float(p.get("expires_in") or 3600), "refreshed_at": time.time()}
    if not new["access_token"]:
        raise LLMError("chatgpt : réponse sans access_token", kind="auth")
    db.set_setting(SETTING, new)  # la rotation remplace l'ancien jeton : on la conserve aussitôt
    if new["refresh_token"] != state["refresh_token"]:
        _give_back_to_codex(state["refresh_token"], new, p.get("id_token") or "")
    return new


async def import_auth(raw: str | dict | None = None) -> dict:
    """Importe les jetons (texte collé, ou ~/.codex/auth.json si rien n'est fourni) et les valide."""
    if raw is None:
        if not codex_file_exists():
            if codex_config().get("cli_auth_credentials_store") in ("keyring", "auto"):
                raise LLMError("Codex range ses identifiants dans le trousseau macOS, pas dans un fichier. Ajoute la ligne "
                               'cli_auth_credentials_store = "file" dans ~/.codex/config.toml, relance « codex login », '
                               "puis réimporte.", kind="auth")
            raise LLMError(f"{codex_file()} introuvable : lance « codex login » (Sign in with ChatGPT) puis réimporte.", kind="auth")
        raw = codex_file().read_text()
    async with _lock:
        await _refresh(parse_auth_json(raw), force=True)
    return status()


def disconnect() -> None:
    db.set_setting(SETTING, None)


async def access() -> tuple[str, str]:
    async with _lock:
        st = stored()
        if not st:
            raise LLMError("Abonnement ChatGPT non connecté (Réglages → Modèles)", kind="auth")
        st = await _refresh(_adopt_codex(st) or st)
        return st["access_token"], st.get("account_id", "")


# ---------------------------------------------------------------------- fournisseur
class ChatGPTProvider:
    kind = "chatgpt"
    name = "chatgpt"

    def __init__(self) -> None:
        self.models = [ModelInfo(id=m, provider="chatgpt", context=272_000) for m in model_names()]

    async def list_models(self) -> list[ModelInfo]:
        return self.models

    def info(self, model: str) -> ModelInfo:
        return next((m for m in self.models if m.id == model), ModelInfo(id=model, provider="chatgpt", context=272_000))

    @staticmethod
    def convert(system: list[str], messages: list[dict], model_ref: str, keep_reasoning: bool = True) -> list[dict]:
        items: list[dict] = []
        sys_text = "\n\n".join(s for s in system if s)
        if sys_text:
            items.append({"type": "message", "role": "system", "content": [{"type": "input_text", "text": sys_text}]})
        pending_images: list[dict] = []

        def flush_images() -> None:
            if pending_images:
                content = [{"type": "input_text", "text": "[Images renvoyées par les outils ci-dessus]"}]
                content += [{"type": "input_image", "image_url": f"data:{i['media_type']};base64,{i['data']}"} for i in pending_images]
                items.append({"type": "message", "role": "user", "content": content})
                pending_images.clear()

        for m in messages:
            if m["role"] != "tool":
                flush_images()
            if m["role"] == "user":
                c = m["content"]
                parts = [{"type": "input_text", "text": c or "…"}] if isinstance(c, str) else [
                    {"type": "input_text", "text": p["text"]} if p["type"] == "text"
                    else {"type": "input_image", "image_url": f"data:{p['media_type']};base64,{p['data']}"} for p in c]
                items.append({"type": "message", "role": "user", "content": parts})
            elif m["role"] == "assistant":
                if keep_reasoning and m.get("model") == model_ref:
                    items.extend(r for r in m.get("thinking") or [] if r.get("type") == "reasoning")
                if m.get("content"):
                    items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": m["content"]}]})
                for tc in m.get("tool_calls") or []:
                    items.append({"type": "function_call", "call_id": tc["id"], "name": tc["name"],
                                  "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False)})
            elif m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m.get("content") or "(vide)"})
                pending_images.extend(m.get("images") or [])
        flush_images()
        return items

    async def chat(self, model: str, system: list[str], messages: list[dict], tools: list[dict] | None = None,
                   on_delta: DeltaCallback = None, max_tokens: int = 0, effort: str = "high") -> LLMResponse:
        try:
            return await self._chat(model, system, messages, tools, on_delta, effort, keep_reasoning=True)
        except LLMError as e:
            # raisonnement rejoué refusé (modèle changé, élément périmé) : on réessaie sans
            if e.status in (400, 404) and re.search(r"reasoning|item", str(e), re.I):
                return await self._chat(model, system, messages, tools, on_delta, effort, keep_reasoning=False)
            raise

    async def _chat(self, model, system, messages, tools, on_delta, effort, keep_reasoning) -> LLMResponse:
        ref = f"chatgpt:{model}"
        token, account = await access()
        body: dict = {"model": model, "instructions": INSTRUCTIONS, "input": self.convert(system, messages, ref, keep_reasoning),
                      "store": False, "stream": True, "include": ["reasoning.encrypted_content"],
                      "reasoning": {"effort": effort if effort in ("low", "medium", "high") else "medium", "summary": "auto"}}
        if tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                              "parameters": t["parameters"], "strict": False} for t in tools]
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = True
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream",
                   "OpenAI-Beta": "responses=experimental", "session_id": str(uuid.uuid4())}
        if account:
            headers["chatgpt-account-id"] = account
        text_parts, calls, reasoning, usage, deltas = [], [], [], {}, []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15)) as c:
                async with c.stream("POST", f"{BASE_URL}/responses", headers=headers, json=body) as r:
                    if r.status_code >= 400:
                        raise self._error(r.status_code, (await r.aread()).decode(errors="replace")[:600])
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            ev = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        t = ev.get("type", "")
                        if t == "response.output_text.delta":
                            deltas.append(ev.get("delta", ""))
                            if on_delta:
                                await on_delta("text", ev.get("delta", ""))
                        elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta") and on_delta:
                            await on_delta("thinking", ev.get("delta", ""))
                        elif t == "response.output_item.done":
                            item = ev.get("item") or {}
                            if item.get("type") == "message":
                                text_parts += [p.get("text", "") for p in item.get("content", []) if p.get("type") == "output_text"]
                            elif item.get("type") == "function_call":
                                calls.append(ToolCall(id=item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                                                      name=item.get("name", ""), arguments=parse_tool_arguments(item.get("arguments"))))
                            elif item.get("type") == "reasoning" and item.get("encrypted_content"):
                                reasoning.append({k: item[k] for k in ("type", "id", "summary", "encrypted_content") if k in item})
                        elif t == "response.completed":
                            usage = (ev.get("response") or {}).get("usage") or {}
                        elif t in ("response.failed", "error", "response.incomplete"):
                            err = (ev.get("response") or {}).get("error") or ev.get("error") or ev
                            msg = json.dumps(err, ensure_ascii=False)[:400]
                            raise LLMError(f"chatgpt : {msg}", retryable="rate" in msg or "overloaded" in msg or "server" in msg,
                                           kind="context" if "context" in msg else "error")
        except httpx.TimeoutException as e:
            raise LLMError(f"chatgpt : délai dépassé ({e.__class__.__name__})", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError(f"chatgpt : erreur réseau {e}", retryable=True) from e
        details = usage.get("input_tokens_details") or {}
        return LLMResponse(text=("".join(text_parts) or "".join(deltas)).strip(), tool_calls=calls, thinking=reasoning,
                           stop_reason="tool_use" if calls else "stop", model=ref,
                           input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
                           cached_tokens=int(details.get("cached_tokens") or 0))

    @staticmethod
    def _error(status: int, detail: str) -> LLMError:
        if status in (401, 403):
            st = stored()
            if st:
                db.set_setting(SETTING, {**st, "expires_at": 0})  # jeton refusé : on le renouvellera au prochain appel
            return LLMError(f"chatgpt : accès refusé ({status}) {detail[:200]}", status=status, kind="auth")
        if status == 400 and "context" in detail.lower():
            return LLMError("chatgpt : contexte trop long", status=status, kind="context")
        return LLMError(f"chatgpt : HTTP {status} {detail[:300]}", status=status, retryable=status == 429 or status >= 500)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise LLMError("chatgpt : pas d'embeddings", kind="not_found")
