"""Abonnement ChatGPT (backend Codex) contre un faux serveur ; rechargement du .env ; moteurs de recherche."""
from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ely.db import db
from ely.llm import LLMError, chatgpt_provider as cg

seen: dict = {"token_calls": []}
mock = FastAPI()


@mock.post("/oauth/token")
async def token(request: Request):
    body = await request.json()
    seen["token_calls"].append(body)
    if body["refresh_token"] == "mort":
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if body["refresh_token"] in seen["spent"]:  # comme chez OpenAI : une clé de renouvellement ne sert qu'une fois
        return JSONResponse({"error": {"code": "refresh_token_reused"}}, status_code=401)
    seen["spent"].add(body["refresh_token"])
    return {"access_token": f"at-{len(seen['token_calls'])}", "refresh_token": f"rt-{len(seen['token_calls'])}", "expires_in": 3600}


@mock.post("/codex/responses")
async def responses(request: Request):
    body = await request.json()
    seen["body"], seen["headers"] = body, dict(request.headers)
    if body["model"] == "refuse":
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    if any(i.get("role") == "system" for i in body["input"]):  # comme le vrai backend depuis GPT-6
        return JSONResponse({"detail": "System messages are not allowed"}, status_code=400)
    if body["model"].startswith("gpt-6") and "parallel_tool_calls" in body:
        return JSONResponse({"detail": "Unsupported parameter: parallel_tool_calls"}, status_code=400)
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": "Je vérifie la météo."},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "CHIFFRE"}},
        {"type": "response.output_text.delta", "delta": "Je "},
        {"type": "response.output_text.delta", "delta": "regarde."},
        {"type": "response.output_item.done", "item": {"type": "message", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "Je regarde."}]}},
        {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_9",
                                                       "name": "weather", "arguments": "{\"location\": \"Lyon\"}"}},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 120, "output_tokens": 30,
                                                              "input_tokens_details": {"cached_tokens": 100}}}},
    ]

    async def gen():
        for e in events:
            yield f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@pytest.fixture(scope="module")
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


@pytest.fixture
def chatgpt(server, monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "BASE_URL", f"{server}/codex")
    monkeypatch.setattr(cg, "TOKEN_URL", f"{server}/oauth/token")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))  # jamais le vrai ~/.codex
    seen["spent"] = set()
    yield
    cg.disconnect()


AUTH_JSON = json.dumps({"OPENAI_API_KEY": None, "tokens": {"id_token": "x", "access_token": "vieux", "refresh_token": "rt-cli",
                                                           "account_id": "acc-42"}, "last_refresh": "2026-09-01"})


async def test_import_refreshes_and_keeps_rotated_token(chatgpt):
    st = await cg.import_auth(AUTH_JSON)
    assert st["connected"] and st["account_id"] == "acc-42"
    assert seen["token_calls"][-1] == {"grant_type": "refresh_token", "client_id": cg.CLIENT_ID, "refresh_token": "rt-cli"}
    stored = db.get_setting(cg.SETTING)
    assert stored["refresh_token"].startswith("rt-") and stored["refresh_token"] != "rt-cli"  # rotation conservée
    with pytest.raises(LLMError):
        await cg.import_auth(json.dumps({"tokens": {"refresh_token": "mort"}}))


async def test_chat_streams_text_tools_and_encrypted_reasoning(chatgpt):
    await cg.import_auth(AUTH_JSON)
    p = cg.ChatGPTProvider()
    deltas = []

    async def on_delta(kind, text):
        deltas.append((kind, text))

    history = [
        {"role": "user", "content": "Météo à Lyon ?"},
        {"role": "assistant", "content": "", "model": "chatgpt:gpt-5.5",
         "thinking": [{"type": "reasoning", "id": "rs_0", "summary": [], "encrypted_content": "ANCIEN"}],
         "tool_calls": [{"id": "call_1", "name": "web_search", "arguments": {"query": "météo Lyon"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "web_search", "content": "12 °C"},
    ]
    tools = [{"name": "weather", "description": "Météo", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}]
    r = await p.chat("gpt-5.5", ["système d'Ely"], history, tools, on_delta)
    assert r.text == "Je regarde." and r.model == "chatgpt:gpt-5.5"
    assert r.tool_calls[0].id == "call_9" and r.tool_calls[0].arguments == {"location": "Lyon"}
    assert r.thinking == [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "CHIFFRE"}]
    assert ("thinking", "Je vérifie la météo.") in deltas and r.cached_tokens == 100
    body, headers = seen["body"], seen["headers"]
    assert body["store"] is False and body["stream"] is True and body["instructions"]
    assert "max_output_tokens" not in body and body["include"] == ["reasoning.encrypted_content"]
    assert headers["chatgpt-account-id"] == "acc-42" and headers["authorization"].startswith("Bearer at-")
    types = [i["type"] for i in body["input"]]
    assert types == ["message", "message", "reasoning", "function_call", "function_call_output"]
    assert body["input"][0]["role"] == "developer" and body["input"][2]["encrypted_content"] == "ANCIEN"
    assert body["input"][4] == {"type": "function_call_output", "call_id": "call_1", "output": "12 °C"}
    assert body["tools"][0]["name"] == "weather" and body["tools"][0]["type"] == "function"


async def test_refused_access_is_an_auth_error(chatgpt):
    await cg.import_auth(AUTH_JSON)
    with pytest.raises(LLMError) as e:
        await cg.ChatGPTProvider().chat("refuse", [], [{"role": "user", "content": "x"}])
    assert e.value.kind == "auth"


async def test_registry_offers_the_subscription_once_connected(chatgpt, fake):
    from ely.llm import registry

    await cg.import_auth(AUTH_JSON)
    registry.build_providers()
    assert "chatgpt" in registry.providers
    registry.providers["fake"] = fake
    await registry.refresh()
    assert any(m["ref"] == "chatgpt:gpt-5.5" for m in registry.all_models())
    cg.disconnect()
    registry.build_providers()
    assert "chatgpt" not in registry.providers


def test_env_reload_picks_up_new_keys(tmp_path, monkeypatch):
    from ely import config

    (tmp_path / ".env").write_text("MISTRAL_API_KEY=cle-toute-neuve\nELY_MAX_STEPS=77\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("ELY_MAX_STEPS", raising=False)
    config._BOOT_ENV.discard("MISTRAL_API_KEY")
    config._BOOT_ENV.discard("ELY_MAX_STEPS")
    try:
        config.reload_env()
        assert config.settings.max_steps == 77
        assert config.settings.openai_compat_keys()["mistral"][1] == "cle-toute-neuve"
    finally:
        os.environ.pop("MISTRAL_API_KEY", None)
        os.environ.pop("ELY_MAX_STEPS", None)
        monkeypatch.undo()
        config.reload_env()


def test_search_chain_uses_every_configured_key(monkeypatch):
    from ely.tools import web

    for k in ("SERPER_API_KEY", "EXA_API_KEY", "SEARCHCANS_API_KEY", "GOOGLE_SEARCH_API_KEY", "GOOGLE_SEARCH_CX"):
        monkeypatch.setenv(k, "x")
    names = [n for n, _ in web._providers()]
    assert names[:4] == ["serper", "exa", "searchcans", "google"] and names[-1] == "ddgs"


def test_codex_model_is_offered(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('model = "gpt-6"\nmodel_reasoning_effort = "high"\n')
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("CHATGPT_MODELS", raising=False)
    assert cg.model_names() == ["gpt-6", "gpt-5.5"]
    assert [m.id for m in cg.ChatGPTProvider().models] == ["gpt-6", "gpt-5.5"]
    assert cg.status()["codex_model"] == "gpt-6"
    monkeypatch.setenv("CHATGPT_MODELS", "gpt-6-mini")
    assert cg.model_names() == ["gpt-6-mini", "gpt-6", "gpt-5.5"]


async def test_missing_auth_file_explains_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with pytest.raises(LLMError) as e:
        await cg.import_auth()
    assert "codex login" in str(e.value)
    (tmp_path / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
    with pytest.raises(LLMError) as e:
        await cg.import_auth()
    assert "trousseau" in str(e.value) and 'cli_auth_credentials_store = "file"' in str(e.value)


def jwt(exp: float) -> str:
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part({'exp': int(exp)})}.sig"


def codex_logs_in(folder, refresh: str, access: str, when: str = "") -> None:
    """Codex écrit sa session, comme après « codex login » ou un renouvellement."""
    folder.mkdir(exist_ok=True)
    (folder / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": when or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tokens": {"id_token": "idt", "access_token": access, "refresh_token": refresh, "account_id": "acc-42"}}))


async def test_ely_and_codex_share_the_subscription_without_cutting_each_other_off(chatgpt, tmp_path):
    """OpenAI remplace la clé de renouvellement à chaque renouvellement. Ely et Codex partagent la même session :
    quand l'un renouvelle, l'autre doit reprendre la nouvelle clé, sinon il est déconnecté pour de bon."""
    codex = tmp_path / "codex"
    codex_logs_in(codex, "rt-codex", "vieux", when="2026-09-01T08:00:00Z")
    await cg.import_auth()  # importée depuis ~/.codex/auth.json : Ely renouvelle aussitôt la session…
    mine = db.get_setting(cg.SETTING)
    shared = json.loads((codex / "auth.json").read_text())
    assert shared["tokens"]["refresh_token"] == mine["refresh_token"] != "rt-codex"  # … et la rend à Codex
    assert shared["tokens"]["access_token"] == mine["access_token"] and shared["auth_mode"] == "chatgpt"
    assert shared["OPENAI_API_KEY"] is None and shared["last_refresh"] > "2026-09-01T08:00:00Z"

    # quelques jours plus tard, Codex renouvelle la session avant Ely : la clé d'Ely ne vaut plus rien
    codex_logs_in(codex, "rt-codex-2", jwt(time.time() + 86400))
    seen["spent"].add(mine["refresh_token"])
    db.set_setting(cg.SETTING, {**mine, "expires_at": 0})
    r = await cg.ChatGPTProvider().chat("gpt-6-astra", [], [{"role": "user", "content": "Bonjour"}])
    assert r.text == "Je regarde."
    assert seen["headers"]["authorization"] == f"Bearer {jwt(time.time() + 86400)}"[:20] + seen["headers"]["authorization"][20:]
    assert db.get_setting(cg.SETTING)["refresh_token"] == "rt-codex-2"


async def test_ely_takes_over_codex_session_once_its_own_key_is_refused(chatgpt, tmp_path):
    """La clé d'Ely est refusée (Codex l'a usée, ou vous vous êtes reconnecté dans Codex) : Ely reprend la session
    de Codex au lieu de basculer sur un autre modèle à chaque appel, et l'écran Modèles la dit connectée."""
    db.set_setting(cg.SETTING, {"access_token": "", "refresh_token": "mort", "account_id": "acc-42", "expires_at": 0,
                                "refreshed_at": time.time() + 3600, "reconnect_required": True})
    assert not cg.status()["connected"]
    codex_logs_in(tmp_path / "codex", "rt-neuve", "vieux", when="2026-09-01T08:00:00Z")
    assert cg.status()["connected"]
    db.set_setting(cg.SETTING, {"access_token": "", "refresh_token": "mort", "account_id": "acc-42", "expires_at": 0,
                                "refreshed_at": time.time() + 3600})
    r = await cg.ChatGPTProvider().chat("gpt-6-astra", [], [{"role": "user", "content": "Bonjour"}])
    assert r.text == "Je regarde." and seen["token_calls"][-1]["refresh_token"] == "rt-neuve"


async def test_expired_subscription_says_how_to_reconnect(chatgpt):
    db.set_setting(cg.SETTING, {"access_token": "", "refresh_token": "mort", "account_id": "acc-42", "expires_at": 0})
    with pytest.raises(LLMError) as e:
        await cg.ChatGPTProvider().chat("gpt-6-astra", [], [{"role": "user", "content": "Bonjour"}])
    assert e.value.kind == "auth" and "expirée" in str(e.value) and "codex login" in str(e.value)


async def test_gpt6_accepts_elys_instructions_and_tools(chatgpt):
    """GPT-6 refuse les messages « system » et, ici, les appels d'outils en parallèle : Ely s'adapte au lieu de
    basculer sur un autre modèle à chaque appel."""
    await cg.import_auth(AUTH_JSON)
    tools = [{"name": "browser", "description": "Navigateur", "parameters": {"type": "object", "properties": {}}}]
    r = await cg.ChatGPTProvider().chat("gpt-6-astra", ["Tu es Ely."], [{"role": "user", "content": "Bonjour"}], tools)
    assert r.text == "Je regarde." and r.tool_calls
    body = seen["body"]
    assert body["input"][0] == {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Tu es Ely."}]}
    assert "parallel_tool_calls" not in body and body["tools"][0]["name"] == "browser"
