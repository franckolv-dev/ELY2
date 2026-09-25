"""Abonnement ChatGPT (backend Codex) contre un faux serveur ; rechargement du .env ; moteurs de recherche."""
from __future__ import annotations

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
    return {"access_token": f"at-{len(seen['token_calls'])}", "refresh_token": f"rt-{len(seen['token_calls'])}", "expires_in": 3600}


@mock.post("/codex/responses")
async def responses(request: Request):
    body = await request.json()
    seen["body"], seen["headers"] = body, dict(request.headers)
    if body["model"] == "refuse":
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
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
def chatgpt(server, monkeypatch):
    monkeypatch.setattr(cg, "BASE_URL", f"{server}/codex")
    monkeypatch.setattr(cg, "TOKEN_URL", f"{server}/oauth/token")
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
    assert body["input"][0]["role"] == "system" and body["input"][2]["encrypted_content"] == "ANCIEN"
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
