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


WEBHOOK_CONFLICT = {"ok": False, "error_code": 409,
                    "description": "Conflict: can't use getUpdates method while webhook is active; use deleteWebhook to delete the webhook first"}


async def _run_polling(monkeypatch, fake_call, seconds):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "jeton")
    monkeypatch.setattr(telegram, "call", fake_call)
    monkeypatch.setattr(telegram, "RETRY_S", 0.05, raising=False)
    task = asyncio.create_task(telegram.polling_loop())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        runner.listeners[:] = [f for f in runner.listeners if f is not telegram.on_event]


async def test_polling_removes_a_webhook_left_by_the_old_version(monkeypatch):
    state = {"webhook": True, "delivered": False}
    handled = []

    async def fake_call(method, **params):
        await asyncio.sleep(0)
        if method == "deleteWebhook":
            state["webhook"] = False
            return {"ok": True, "result": True}
        if method == "getUpdates":
            if state["webhook"]:
                return WEBHOOK_CONFLICT
            if not state["delivered"]:
                state["delivered"] = True
                return {"ok": True, "result": [{"update_id": 7, "message": {"chat": {"id": 1}, "text": "coucou"}}]}
            await asyncio.sleep(3600)
        return {"ok": True, "result": {"username": "ely_bot"}}

    async def fake_handle(upd):
        handled.append(upd["update_id"])

    monkeypatch.setattr(telegram, "handle", fake_handle)
    await _run_polling(monkeypatch, fake_call, 0.3)
    assert handled == [7]


async def test_polling_waits_after_a_refusal(monkeypatch):
    count = {"getUpdates": 0, "deleteWebhook": 0}

    async def fake_call(method, **params):
        await asyncio.sleep(0)
        count[method] = count.get(method, 0) + 1
        if method == "getUpdates":
            return WEBHOOK_CONFLICT  # webhook remis en place par un autre programme à chaque fois
        return {"ok": True, "result": {}}

    await _run_polling(monkeypatch, fake_call, 0.4)
    assert 2 <= count["getUpdates"] <= 12  # pas de boucle folle contre l'API
    assert count["deleteWebhook"] >= count["getUpdates"]
