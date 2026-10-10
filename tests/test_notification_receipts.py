"""Accusés Telegram réels, sans réseau ni jeton réel ; aucun contrôle navigateur requis."""
import json
from types import SimpleNamespace

import httpx
import pytest

from ely import notify as notifications
from ely.integrations import put
from ely.tools import planning


@pytest.fixture
def transport(monkeypatch):
    client = httpx.AsyncClient
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "synthetic-secret")

    def install(handler):
        monkeypatch.setattr(notifications.httpx, "AsyncClient",
                            lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return install


@pytest.mark.parametrize("text", ["simple", "a" * 8001, "😀" * 4001, "é\n😀" * 3000, ""])
def test_telegram_chunks_preserve_text_and_utf16_limit(text):
    chunks = notifications.telegram_chunks(text)
    assert "".join(chunks) == text
    assert all(0 < len(c.encode("utf-16-le")) // 2 <= 4000 for c in chunks)


async def test_telegram_success_returns_all_ids_and_full_text(transport):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(bodies)}})

    transport(handler)
    text = "Résumé 😀\n" * 1100 + "FIN"
    ids = await notifications.telegram_send(123, text)
    assert ids == list(range(1, len(bodies) + 1))
    assert len(ids) > 1
    assert "".join(b["text"] for b in bodies) == text
    assert all(b["chat_id"] == 123 for b in bodies)


@pytest.mark.parametrize("status,data", [
    (403, {"description": "synthetic-secret"}),
    (429, {"description": "synthetic-secret"}),
    (200, {"ok": False, "description": "synthetic-secret"}),
    (200, {"ok": True, "result": {}}),
    (200, {"ok": True, "result": {"message_id": True}}),
    (200, {"ok": True, "result": {"message_id": -1}}),
    (200, []),
])
async def test_telegram_rejects_http_api_and_invalid_receipts(transport, status, data):
    transport(lambda request: httpx.Response(status, json=data))
    with pytest.raises(notifications.TelegramSendError) as caught:
        await notifications.telegram_send(123, "hello")
    assert caught.value.message_ids == []
    assert caught.value.total == 1
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("failure", ["http", "timeout", "json"])
async def test_partial_send_keeps_ids_and_never_retries(transport, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-secret", request=request)
        if failure == "json":
            return httpx.Response(200, text="synthetic-secret not JSON")
        return httpx.Response(500)

    transport(handler)
    with pytest.raises(notifications.TelegramSendError) as caught:
        await notifications.telegram_send(123, "x" * 9000)
    assert caught.value.message_ids == [42]
    assert caught.value.total == 3
    assert len(calls) == 2  # ni troisième partie, ni répétition de la première
    assert "synthetic-secret" not in str(caught.value)


async def test_missing_token_not_counted_as_success(user, monkeypatch):
    put(user["id"], "telegram", {"chat_id": 123})
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "")
    receipt = {}
    assert await notifications.notify(user["id"], "title", "body", receipt=receipt) == 0
    assert receipt["telegram_status"] == "unconfirmed"
    assert receipt["telegram_error"] == "bot non configuré"


async def test_empty_telegram_text_is_not_success(transport):
    def handler(request):
        pytest.fail("aucune requête pour un texte vide")
    transport(handler)
    with pytest.raises(notifications.TelegramSendError, match="texte vide"):
        await notifications.telegram_send(123, "")


async def test_notify_int_contract_and_detailed_success(user, transport):
    put(user["id"], "telegram", {"chat_id": 123})
    transport(lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 55}}))
    receipt = {}
    assert await notifications.notify(user["id"], "title", "body", receipt=receipt) == 1
    assert receipt["telegram_status"] == "accepted"
    assert receipt["telegram_ids"] == [55]
    assert receipt["push_accepted"] == receipt["push_total"] == 0
    assert await notifications.notify(user["id"], "title", "body") == 1


async def test_push_success_does_not_hide_telegram_failure(user, transport, monkeypatch, caplog):
    put(user["id"], "telegram", {"chat_id": 123})
    notifications.save_subscription(user["id"], {"endpoint": "https://push.example.invalid/test"})
    monkeypatch.setattr(notifications, "vapid_keys", lambda: {})
    monkeypatch.setattr(notifications, "_push_sync", lambda *args: 201)
    transport(lambda request: httpx.Response(403, json={"description": "synthetic-secret"}))
    ctx = SimpleNamespace(user_id=user["id"], conversation_id=1)
    with caplog.at_level("INFO", logger="ely.notify"):
        result = await planning.notify(ctx, "body")
    assert result.is_error
    assert "Push : 1/1" in result.content
    assert "Telegram : 0/1" in result.content
    assert "Ne pas renvoyer automatiquement" in result.content
    assert "synthetic-secret" not in result.content + caplog.text


async def test_tool_reports_partial_message_ids(user, transport):
    put(user["id"], "telegram", {"chat_id": 123})
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 81}})
        return httpx.Response(429)

    transport(handler)
    result = await planning.notify(SimpleNamespace(user_id=user["id"], conversation_id=1), "x" * 9000)
    assert result.is_error
    assert "Telegram : 1/3" in result.content
    assert "message_id : 81" in result.content
    assert len(calls) == 2


async def test_tool_receipt_distinguishes_acceptance_from_reading(user, transport):
    put(user["id"], "telegram", {"chat_id": 123})
    transport(lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 77}}))
    result = await planning.notify(SimpleNamespace(user_id=user["id"], conversation_id=1), "body")
    assert not result.is_error
    assert "message_id : 77" in result.content
    assert "lecture par le destinataire inconnue" in result.content
    assert "Pas de contrôle supplémentaire" in result.content


async def test_no_subscription_is_explicit_error(user):
    result = await planning.notify(SimpleNamespace(user_id=user["id"], conversation_id=1), "body")
    assert result.is_error
    assert "Telegram : non relié" in result.content
    assert "Aucune destination entièrement confirmée" in result.content
