"""Environnement de test : données temporaires, aucun vrai modèle, fournisseur simulé scriptable."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ely-tests-")
os.environ["ELY_DATA_DIR"] = _TMP
for k in list(os.environ):
    if k.endswith("_API_KEY") or k in ("LMSTUDIO_BASE_URL", "TELEGRAM_BOT_TOKEN", "GOOGLE_CLIENT_ID"):
        os.environ.pop(k)
os.environ["LMSTUDIO_BASE_URL"] = "http://127.0.0.1:9/v1"  # injoignable exprès
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
