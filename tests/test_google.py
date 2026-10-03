"""Gmail : une recherche large ne se fait pas refuser par Google pour trop de requêtes simultanées."""
from __future__ import annotations

import asyncio
import time

import httpx

from ely.integrations import google, put


def fake_gmail(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(google.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


async def test_wide_gmail_search_is_not_refused(user, monkeypatch):
    put(user["id"], "google", {"access_token": "jeton", "refresh_token": "", "expires_at": time.time() + 3600})
    state = {"live": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": f"m{i}"} for i in range(100)]})
        state["live"] += 1
        try:
            await asyncio.sleep(0.01)
            if state["live"] > 10:  # comme Gmail : « Too many concurrent requests for user »
                return httpx.Response(429, json={"error": {"code": 429, "message": "Too many concurrent requests for user."}})
            return httpx.Response(200, json={"snippet": "x", "labelIds": [], "payload": {"headers": [
                {"name": "Subject", "value": request.url.path.rsplit("/", 1)[-1]}]}})
        finally:
            state["live"] -= 1

    fake_gmail(monkeypatch, handler)
    found = await google.gmail_search(user["id"], "in:anywhere newer_than:60d", 100)
    assert len(found) == 100 and all(m["subject"] == m["id"] for m in found)


async def test_email_manage_trashes_and_files_invoices_in_gmail(user, monkeypatch):
    """La routine de midi trie la boîte par l'API : corbeille, et factures classées sous un libellé (créé s'il manque)."""
    import json

    from conftest import new_conversation

    from ely.tools import ToolContext, execute

    async def _noop(*a, **k):
        pass

    put(user["id"], "google", {"access_token": "jeton", "refresh_token": "", "expires_at": time.time() + 3600})
    seen: list[tuple] = []
    labels = [{"id": "INBOX", "name": "INBOX"}, {"id": "Label_7", "name": "Factures"}]

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path.split("/users/me")[-1], body))
        if request.url.path.endswith("/labels") and request.method == "GET":
            return httpx.Response(200, json={"labels": labels})
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"id": "Label_9", "name": body["name"]})
        return httpx.Response(200, json={} if request.url.path.endswith("batchModify") else {"id": "x"})

    fake_gmail(monkeypatch, handler)
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)

    r = await execute(ctx, "email_manage", {"ids": ["a1", "a2"], "action": "trash"})
    assert not r.is_error and "2 e-mail(s)" in r.content
    assert sorted(p for m, p, b in seen) == ["/messages/a1/trash", "/messages/a2/trash"]

    seen.clear()
    r = await execute(ctx, "email_manage", {"ids": ["f1"], "action": "label", "label": "factures"})
    assert not r.is_error, r.content
    assert seen[-1] == ("POST", "/messages/batchModify", {"ids": ["f1"], "addLabelIds": ["Label_7"], "removeLabelIds": ["INBOX"]})

    seen.clear()
    r = await execute(ctx, "email_manage", {"ids": ["f2"], "action": "label", "label": "Factures/2026"})
    assert ("POST", "/labels", {"name": "Factures/2026"}) in seen
    assert seen[-1][2]["addLabelIds"] == ["Label_9"]

    r = await execute(ctx, "email_manage", {"ids": ["f3"], "action": "label"})
    assert r.is_error


async def test_gmail_request_refused_for_load_is_retried(user, monkeypatch):
    put(user["id"], "google", {"access_token": "jeton", "refresh_token": "", "expires_at": time.time() + 3600})
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "Too many concurrent requests for user."}})
        return httpx.Response(200, json={"messages": []})

    fake_gmail(monkeypatch, handler)
    assert await google.gmail_search(user["id"], "from:digiposte", 10) == []
    assert len(calls) == 2
