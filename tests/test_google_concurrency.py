"""Budget Gmail partagé entre outils et sous-agents, avec HTTP entièrement simulé."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from ely.integrations import google


def mock_api(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(google.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def token(user_id):
        return f"test-user-{user_id}"

    monkeypatch.setattr(google, "_token", token)


async def test_simultaneous_search_trash_read_and_archive_share_budget(monkeypatch):
    live = peak = 0
    calls = []

    async def handler(request):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        calls.append((request.method, request.url.path))
        try:
            await asyncio.sleep(0.002)
            if request.url.path.endswith("/messages"):
                return httpx.Response(200, json={"messages": [{"id": f"m{i}"} for i in range(20)]})
            return httpx.Response(200, json={"payload": {"headers": []}})
        finally:
            live -= 1

    mock_api(monkeypatch, handler)
    results = await asyncio.gather(
        google.gmail_search(1, "first", 20),
        google.gmail_search(1, "second", 20),
        google.gmail_manage(1, [f"trash-{i}" for i in range(20)], "trash"),
        google.gmail_read(1, "read"),
        google.gmail_manage(1, ["archive"], "archive"),
    )
    assert len(results[0]) == len(results[1]) == 20
    assert peak == google.GMAIL_PARALLEL
    assert live == 0
    assert len(calls) == 64  # toutes les opérations, pas de requête abandonnée ni de reprise cachée
    assert ("POST", "/gmail/v1/users/me/messages/batchModify") in calls


async def test_saturated_user_does_not_block_other_user_or_calendar(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def handler(request):
        nonlocal count
        if request.url.host == "gmail.googleapis.com" and request.headers["Authorization"].endswith("test-user-1"):
            count += 1
            if count == google.GMAIL_PARALLEL:
                entered.set()
            await release.wait()
        return httpx.Response(200, json={})

    mock_api(monkeypatch, handler)
    busy = [asyncio.create_task(google.api(1, "GET", google.GMAIL + "/messages/x"))
            for _ in range(google.GMAIL_PARALLEL)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        other, calendar = await asyncio.wait_for(asyncio.gather(
            google.api(2, "GET", google.GMAIL + "/messages/x"),
            google.api(1, "GET", google.CAL),
        ), 2)
        assert other == calendar == {}
    finally:
        release.set()
        await asyncio.gather(*busy)


async def test_cancellation_releases_gmail_slot(monkeypatch):
    monkeypatch.setattr(google, "GMAIL_PARALLEL", 1)
    entered = asyncio.Event()

    async def handler(request):
        if request.url.path.endswith("/blocked"):
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={})

    mock_api(monkeypatch, handler)
    gate = google._gmail_limiter(101)  # conserver le même sémaphore pour la vérification
    task = asyncio.create_task(google.api(101, "GET", google.GMAIL + "/messages/blocked"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert gate.locked()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not gate.locked()
    assert await asyncio.wait_for(google.api(101, "GET", google.GMAIL + "/messages/ok"), 2) == {}


async def test_http_error_releases_slot_without_hiding_error(monkeypatch):
    monkeypatch.setattr(google, "GMAIL_PARALLEL", 1)
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(404, json={"error": "not found"}) if len(calls) == 1 else httpx.Response(200, json={})

    mock_api(monkeypatch, handler)
    gate = google._gmail_limiter(102)
    with pytest.raises(RuntimeError, match="Google API 404"):
        await google.api(102, "GET", google.GMAIL + "/messages/missing")
    assert not gate.locked()
    assert await asyncio.wait_for(google.api(102, "GET", google.GMAIL + "/messages/ok"), 2) == {}
    assert len(calls) == 2


def test_limiter_is_not_reused_across_event_loops():
    async def obtain():
        return google._gmail_limiter(103)

    first = asyncio.run(obtain())
    second = asyncio.run(obtain())
    assert first is not second
