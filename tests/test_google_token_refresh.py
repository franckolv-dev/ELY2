"""Renouvellement OAuth : concurrence, erreurs bornées et reconnexion, HTTP simulé."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from weakref import WeakKeyDictionary

import httpx
import pytest

from ely.integrations import google


@pytest.fixture
def tokens(monkeypatch):
    store = {u: {"access_token": f"old-{u}", "refresh_token": f"refresh-{u}", "expires_at": 0}
             for u in (1, 2)}
    monkeypatch.setattr(google, "get", lambda u, s: dict(store[u]) if u in store else None)
    monkeypatch.setattr(google, "put", lambda u, s, d: store.__setitem__(u, dict(d)))
    monkeypatch.setattr(google, "_token_failures", OrderedDict())
    monkeypatch.setattr(google, "_token_locks", WeakKeyDictionary())
    return store


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(google.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


async def test_parallel_refresh_only_posts_once(monkeypatch, tokens):
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.005)
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600})

    mock_http(monkeypatch, handler)
    assert await asyncio.gather(*(google._token(1) for _ in range(20))) == ["new"] * 20
    assert len(calls) == 1
    assert tokens[1]["refresh_token"] == "rotated"


async def test_parallel_invalid_grant_is_cached_with_same_error(monkeypatch, tokens):
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.005)
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "private-provider-text"})

    mock_http(monkeypatch, handler)
    errors = await asyncio.gather(*(google._token(1) for _ in range(20)), return_exceptions=True)
    assert len(calls) == 1
    assert all(isinstance(e, RuntimeError) and str(e) == google.RECONNECT for e in errors)
    assert "refresh-1" not in repr(google._token_failures)
    assert "private-provider-text" not in str(errors)
    with pytest.raises(RuntimeError, match="invalid_grant"):
        await google._token(1)
    assert len(calls) == 1


async def test_cache_expires_and_is_isolated_by_user(monkeypatch, tokens):
    clock = [100.0]
    # Patch only the google module's clock object, not asyncio's shared time module.
    from types import SimpleNamespace
    monkeypatch.setattr(google, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: clock[0]))
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "invalid_grant"})

    mock_http(monkeypatch, handler)
    for uid in (1, 1, 2):
        with pytest.raises(RuntimeError, match="invalid_grant"):
            await google._token(uid)
    assert len(calls) == 2
    clock[0] += google.TOKEN_FAILURE_TTL
    with pytest.raises(RuntimeError, match="invalid_grant"):
        await google._token(1)
    assert len(calls) == 3


@pytest.mark.parametrize("status,body", [(503, {"error": "invalid_grant"}), (429, {}),
                                          (400, {"error": "invalid_client"}), (502, None)])
async def test_other_http_errors_do_not_request_reconnect_or_cache(monkeypatch, tokens, status, body):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="bad gateway")
        return httpx.Response(200, json={"access_token": "recovered"})

    mock_http(monkeypatch, handler)
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as err:
        await google._token(1)
    assert "Reconnexion Google nécessaire" not in str(err.value)
    assert not google._token_failures
    assert await google._token(1) == "recovered"
    assert len(calls) == 2


async def test_network_error_is_not_cached(monkeypatch, tokens):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"access_token": "recovered"})

    mock_http(monkeypatch, handler)
    with pytest.raises(httpx.ConnectError):
        await google._token(1)
    assert not google._token_failures
    assert await google._token(1) == "recovered"


@pytest.mark.parametrize("fresh_access", [False, True])
async def test_new_credentials_immediately_invalidate_failure(monkeypatch, tokens, fresh_access):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "new"})

    mock_http(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="invalid_grant"):
        await google._token(1)
    if fresh_access:
        tokens[1].update(access_token="new", expires_at=google.time.time() + 3600)
    else:
        tokens[1]["refresh_token"] = "reconnected"
    assert await google._token(1) == "new"
    assert len(calls) == (1 if fresh_access else 2)
    assert not google._token_failures


@pytest.mark.parametrize("disconnect", [False, True])
async def test_connection_change_during_http_is_not_overwritten(monkeypatch, tokens, disconnect):
    async def handler(request):
        if disconnect:
            tokens.pop(1)
        else:
            tokens[1].update(access_token="reconnected", expires_at=google.time.time() + 3600)
        return httpx.Response(200, json={"access_token": "stale-http-result"})

    mock_http(monkeypatch, handler)
    if disconnect:
        with pytest.raises(RuntimeError, match="non connecté"):
            await google._token(1)
        assert 1 not in tokens
    else:
        assert await google._token(1) == "reconnected"
        assert tokens[1]["access_token"] == "reconnected"


async def test_cancellation_releases_lock_and_other_users_proceed(monkeypatch, tokens):
    entered = asyncio.Event()

    async def handler(request):
        if b"refresh-1" in request.content:
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"access_token": "other-user"})

    mock_http(monkeypatch, handler)
    lock = google._token_lock(1)
    task = asyncio.create_task(google._token(1))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert lock.locked()
        assert await asyncio.wait_for(google._token(2), 2) == "other-user"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not lock.locked()
    assert not google._token_failures
    tokens[1].update(access_token="reconnected", expires_at=google.time.time() + 3600)
    assert await asyncio.wait_for(google._token(1), 2) == "reconnected"


async def test_negative_cache_is_bounded(monkeypatch, tokens):
    monkeypatch.setattr(google, "TOKEN_FAILURE_LIMIT", 1)

    async def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    mock_http(monkeypatch, handler)
    for uid in (1, 2):
        with pytest.raises(RuntimeError, match="invalid_grant"):
            await google._token(uid)
    assert list(google._token_failures) == [2]


def test_token_lock_is_not_reused_across_event_loops(tokens):
    async def obtain():
        return google._token_lock(1)

    first = asyncio.run(obtain())
    second = asyncio.run(obtain())
    assert first is not second
