"""Canal Telegram : liaison du compte, message entrant traité par l'agent, réponse renvoyée."""
from __future__ import annotations

import asyncio

from conftest import wait_idle

from ely.agent.runner import runner
from ely.channels import telegram
from ely.integrations import get, put


async def test_telegram_link_and_conversation(fake, user, monkeypatch):
    sent: list[tuple] = []

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    async def fake_call(method, **params):
        return {"ok": True, "result": {}}

    monkeypatch.setattr(telegram, "telegram_send", fake_send)
    monkeypatch.setattr(telegram, "call", fake_call)
    runner.listeners.append(telegram.on_event)
    try:
        put(user["id"], "telegram_pending", {"code": "abc123", "at": 0})
        await telegram.handle({"update_id": 1, "message": {"chat": {"id": 555}, "from": {"username": "franck"}, "text": "/start abc123"}})
        assert get(user["id"], "telegram")["chat_id"] == 555
        assert "relié" in sent[-1][1]

        fake.script = lambda model, system, messages, tools: "Il fait beau à Lyon ☀️"
        await telegram.handle({"update_id": 2, "message": {"chat": {"id": 555}, "from": {}, "text": "Quel temps à Lyon ?"}})
        cid = telegram.conversation_for(user)
        await wait_idle(cid)
        await asyncio.sleep(0.2)
        assert (555, "Il fait beau à Lyon ☀️") in sent

        await telegram.handle({"update_id": 3, "message": {"chat": {"id": 999}, "from": {}, "text": "coucou"}})
        assert sent[-1][0] == 999 and "pas relié" in sent[-1][1]
    finally:
        runner.listeners.remove(telegram.on_event)
