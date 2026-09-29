"""Environnement de test : données temporaires, aucun vrai modèle, fournisseur simulé scriptable."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ely-tests-")
os.environ["ELY_DATA_DIR"] = _TMP
for k in list(os.environ):
    if k.endswith("_API_KEY") or k in ("LMSTUDIO_BASE_URL", "TELEGRAM_BOT_TOKEN", "GOOGLE_CLIENT_ID", "CLAUDE_CODE_OAUTH_TOKEN"):
        os.environ.pop(k)
os.environ["LMSTUDIO_BASE_URL"] = "http://127.0.0.1:9/v1"  # injoignable exprès
os.environ["XTTS_URL"] = "http://127.0.0.1:9"  # idem : le vrai service vocal du Mac ne répond pas aux tests
_chrome = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
if Path(_chrome).exists() and not os.environ.get("ELY_BROWSER_EXECUTABLE"):
    os.environ["ELY_BROWSER_EXECUTABLE"] = _chrome

import asyncio  # noqa: E402
from typing import Callable  # noqa: E402

import pytest  # noqa: E402

from ely.llm.base import LLMError, LLMResponse, ModelInfo, ToolCall  # noqa: E402


class FakeProvider:
    """Fournisseur de modèle simulé : un script décide de chaque réponse."""

    kind = "openai"

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.script: Callable | None = None
        self.calls: list[dict] = []
        self.models = [ModelInfo(id="agent", provider=name, context=200_000), ModelInfo(id="fast", provider=name)]

    async def list_models(self):
        return self.models

    def info(self, model):
        return next((m for m in self.models if m.id == model), ModelInfo(id=model, provider=self.name))

    async def chat(self, model, system, messages, tools=None, on_delta=None, max_tokens=0, effort="high"):
        self.calls.append({"model": model, "system": system, "messages": messages, "tools": [t["name"] for t in tools or []]})
        res = self.script(model=model, system=system, messages=messages, tools=tools or [])
        if asyncio.iscoroutine(res):
            res = await res
        if isinstance(res, Exception):
            raise res
        if isinstance(res, str):
            res = LLMResponse(text=res)
        res.model = f"{self.name}:{model}"
        if on_delta and res.text:
            for i in range(0, len(res.text), 8):
                await on_delta("text", res.text[i:i + 8])
        return res

    async def embed(self, model, texts):
        raise LLMError("pas d'embeddings", kind="not_found")


def call(_tool: str, **args) -> LLMResponse:
    return LLMResponse(text="", tool_calls=[ToolCall(id=f"call_{_tool}_{abs(hash(str(args))) % 10**8}", name=_tool, arguments=args)])


def last_user_text(messages) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            c = m["content"]
            return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


@pytest.fixture
def fake():
    from ely.db import db
    from ely.llm import registry

    prov = FakeProvider()
    registry.providers = {"fake": prov}
    registry.catalog = {"fake": prov.models}
    registry.status = {"fake": "ok (2 modèles)"}
    registry.refreshed_at = 10**12
    for role, val in {"main": "fake:agent", "fast": "fake:fast", "local": "fake:fast", "embed": "", "strong": "", "fallbacks": ""}.items():
        db.set_setting(f"model_{role}", val or "auto")
    yield prov


@pytest.fixture
def user():
    from ely import auth
    from ely.db import db

    email = f"u{os.urandom(3).hex()}@test.fr"
    u = auth.create_user(email, "Franck", "motdepasse")
    if u["role"] != "admin":
        db.run("UPDATE users SET role = 'admin' WHERE id = ?", (u["id"],))
        u = auth.get_user(u["id"])
    return u


@pytest.fixture(autouse=True, scope="session")
def _tools():
    from ely.tools import load_builtin_tools

    load_builtin_tools()
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Petit dépôt git (une addition boguée et son test) à la place du code d'Ely, pour l'auto-modification."""
    import subprocess

    from ely.selfdev import pipeline

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    monkeypatch.setattr(pipeline, "ROOT", repo)
    monkeypatch.setattr(pipeline, "WORKTREE", tmp_path / "wt")
    return repo


def new_conversation(user) -> int:
    from ely.db import db, now

    return db.insert("conversations", user_id=user["id"], title="Nouvelle conversation", created_at=now(), updated_at=now())


async def wait_idle(conv_id: int, timeout: float = 10) -> None:
    from ely.agent.runner import runner

    t = 0.0
    await asyncio.sleep(0.05)
    while t < timeout:
        st = runner.states.get(conv_id)
        if not st or not st.task or st.task.done():
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.05)
        t += 0.05
    raise TimeoutError("la tâche ne se termine pas")


def _wav(seconds: float = 0.2) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(b"\x00\x00" * int(24_000 * seconds))
    return buf.getvalue()


@pytest.fixture
async def xtts(monkeypatch):
    """Faux service vocal XTTS du Mac (même contrat que voice/xtts de l'ancienne version) : note ce qu'on lui fait lire.
    Un texte contenant « panne » le fait échouer."""
    import socket

    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import Response

    from ely.config import settings

    app, said = FastAPI(), []

    @app.get("/voices")
    def voices():
        return {"voices": ["gert"], "default_voice": "gert"}

    @app.post("/speak")
    def speak(body: dict):
        if "panne" in body["text"]:
            raise HTTPException(500, "MPS indisponible")
        said.append(body)
        return Response(_wav(), media_type="audio/wav")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    monkeypatch.setattr(settings, "xtts_url", f"http://127.0.0.1:{port}")
    yield said
    server.should_exit = True
    await task
