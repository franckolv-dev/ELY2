"""Adaptateurs de modèles, testés contre de faux serveurs HTTP (format OpenAI et API Anthropic)."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ely.llm.anthropic_provider import AnthropicProvider
from ely.llm.base import LLMError, parse_json_loose
from ely.llm.openai_compat import OpenAICompatProvider, ThinkFilter

received: dict = {}


def sse(events):
    async def gen():
        for e in events:
            yield e
    return StreamingResponse(gen(), media_type="text/event-stream")


mock = FastAPI()


@mock.post("/v1/chat/completions")
async def openai_chat(request: Request):
    body = await request.json()
    received["openai"] = body
    if body["model"] == "rate-limited":
        return JSONResponse({"error": "slow down"}, status_code=429)
    chunks = [
        {"choices": [{"delta": {"content": "<thi"}}]},
        {"choices": [{"delta": {"content": "nk>je réfléchis</think>Voici "}}]},
        {"choices": [{"delta": {"content": "la météo."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "weather", "arguments": '{"loca'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'tion": "Lyon"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
    ]
    return sse([f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"])


@mock.get("/v1/models")
async def models():
    return {"data": [{"id": "qwen3"}, {"id": "text-embedding-nomic"}]}


@mock.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    return {"data": [{"index": i, "embedding": [0.1, 0.2, float(i)]} for i, _ in enumerate(body["input"])]}


@mock.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    received["anthropic"] = body
    received["anthropic_headers"] = dict(request.headers)

    def ev(name, data):
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"], "content": [],
           "stop_reason": None, "stop_sequence": None,
           "usage": {"input_tokens": 50, "output_tokens": 1, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 0}}
    events = [
        ev("message_start", {"type": "message_start", "message": msg}),
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Il faut la météo."}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig123"}}),
        ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ev("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Je regarde."}}),
        ev("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ev("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {}}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"location": '}}),
        ev("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}}),
        ev("content_block_stop", {"type": "content_block_stop", "index": 2}),
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 25}}),
        ev("message_stop", {"type": "message_stop"}),
    ]
    return sse(events)


@pytest.fixture(scope="module")
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


HISTORY = [
    {"role": "user", "content": "Météo à Lyon ?"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "old1", "name": "web_search", "arguments": {"query": "x"}}],
     "thinking": [{"type": "thinking", "thinking": "t", "signature": "s"}], "model": "anthropic:claude-opus-5"},
    {"role": "tool", "tool_call_id": "old1", "name": "web_search", "content": "résultats",
     "images": [{"media_type": "image/jpeg", "data": "QUJD"}]},
    {"role": "user", "content": "et demain ?", "kind": "control"},
]


async def test_openai_compat_stream_with_think_tags_and_tools(server):
    p = OpenAICompatProvider("lmstudio", f"{server}/v1", "k")
    deltas = []

    async def on_delta(kind, text):
        deltas.append((kind, text))

    tools = [{"name": "weather", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    r = await p.chat("gemma-4-26b", ["système"], HISTORY, tools, on_delta)
    assert r.text == "Voici la météo."
    assert r.tool_calls[0].name == "weather" and r.tool_calls[0].arguments == {"location": "Lyon"}
    assert ("thinking", "je réfléchis") in deltas
    assert r.input_tokens == 12 and r.model == "lmstudio:gemma-4-26b"
    body = received["openai"]
    assert body["messages"][0] == {"role": "system", "content": "système"}
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user", "user"]  # image d'outil → message utilisateur
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"query": "x"}'


async def test_openai_compat_errors_and_models(server):
    p = OpenAICompatProvider("groq", f"{server}/v1", "k")
    with pytest.raises(LLMError) as e:
        await p.chat("rate-limited", [], [{"role": "user", "content": "x"}])
    assert e.value.retryable
    models = await p.list_models()
    assert {m.id for m in models} == {"qwen3", "text-embedding-nomic"}
    assert [m.kind for m in models if m.id.startswith("text")] == ["embeddings"]
    vecs = await p.embed("text-embedding-nomic", ["a", "b"])
    assert len(vecs) == 2


def test_mistral_tool_ids_are_normalized():
    p = OpenAICompatProvider("mistral", "http://x", "k")
    msgs = p.convert_messages([], HISTORY, vision=True)
    tid = msgs[1]["tool_calls"][0]["id"]
    assert len(tid) == 9 and msgs[2]["tool_call_id"] == tid


def test_anthropic_conversion():
    out = AnthropicProvider.convert_messages(HISTORY, "anthropic:claude-opus-5")
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"][0]["type"] == "thinking"  # même modèle : réflexion conservée
    user_blocks = out[2]["content"]
    assert user_blocks[0]["type"] == "tool_result" and user_blocks[-1]["type"] == "text"
    assert user_blocks[0]["content"][1]["type"] == "image"
    other = AnthropicProvider.convert_messages(HISTORY, "anthropic:claude-sonnet-5")
    assert other[1]["content"][0]["type"] == "tool_use"  # autre modèle : réflexion retirée


async def test_anthropic_stream_via_sdk(server):
    p = AnthropicProvider("sk-test", base_url=server)
    deltas = []

    async def on_delta(kind, text):
        deltas.append((kind, text))

    tools = [{"name": "weather", "description": "Météo", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}]
    r = await p.chat("claude-opus-5", ["stable", "dynamique"], HISTORY, tools, on_delta)
    assert r.text == "Je regarde."
    assert r.tool_calls[0].arguments == {"location": "Paris"}
    assert r.thinking and r.thinking[0]["signature"] == "sig123"
    assert ("thinking", "Il faut la météo.") in deltas and ("text", "Je regarde.") in deltas
    assert r.cached_tokens == 30 and r.input_tokens == 80
    body = received["anthropic"]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["thinking"]["type"] == "adaptive" and body["output_config"]["effort"] == "high"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in received["anthropic_headers"].get("anthropic-beta", "")
    assert body["tools"][0]["input_schema"]["properties"]["location"]["type"] == "string"
    assert len(body["system"]) == 2
    # Haiku : ni réflexion adaptative ni effort, API non bêta
    await p.chat("claude-haiku-4-5", ["s"], [{"role": "user", "content": "x"}], None)
    body = received["anthropic"]
    assert "thinking" not in body and "output_config" not in body and "fallbacks" not in body
    await p.client.close()  # sinon le ramasse-miettes le ferme pendant un autre test, sur une boucle déjà fermée


def test_think_filter_split_tags():
    f = ThinkFilter()
    out = []
    for chunk in ["Bon", "jour <th", "ink>secret</th", "ink> fin"]:
        out += f.feed(chunk)
    out += f.flush()
    text = "".join(t for k, t in out if k == "text")
    think = "".join(t for k, t in out if k == "thinking")
    assert text == "Bonjour  fin" and think == "secret"


def test_parse_json_loose():
    assert parse_json_loose('Voici : ```json\n{"done": false, "missing": "a : b"}\n```') == {"done": False, "missing": "a : b"}
    assert parse_json_loose('<think>hmm</think> {"a": {"b": "}"}} fin') == {"a": {"b": "}"}}


async def test_anthropic_drops_rejected_optional_features(server):
    """Si l'API refuse une option récente, Ely la retire et réessaie au lieu d'abandonner Claude."""
    import anthropic

    p = AnthropicProvider("sk-test", base_url=server)
    calls = []
    real = p._chat

    async def flaky(model, ref, system, messages, tools, on_delta, max_tokens, effort, keep_thinking):
        calls.append(set(p.disabled))
        if "fallbacks" not in p.disabled:
            raise LLMError("anthropic: requête refusée (Unexpected beta header server-side-fallback)", status=400)
        return await real(model, ref, system, messages, tools, on_delta, max_tokens, effort, keep_thinking)

    p._chat = flaky
    r = await p.chat("claude-opus-5", ["s"], [{"role": "user", "content": "x"}], None)
    assert r.text == "Je regarde." and "fallbacks" in p.disabled and len(calls) == 2
    assert anthropic  # le SDK reste la voie d'appel
    await p.client.close()
