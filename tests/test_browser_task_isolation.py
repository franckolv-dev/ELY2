"""Deux routines alternent leurs actions : aucun clic ne change de page à leur insu.

Transport navigateur simulé, outil réel ; aucun service ni compte externe.
"""
import asyncio
from types import SimpleNamespace

import pytest

from ely.browser import BaseUserBrowser, manager
from ely.tools import ToolContext
from ely.tools.browser import _browser


async def noop(*args, **kwargs):
    pass


class Page:
    def __init__(self):
        self.url = "about:blank"
        self.clicks = []

    async def goto(self, url, **kwargs):
        self.url = url

    async def evaluate(self, js, *args):
        return bool(args)  # ref présente ; aucun bandeau à fermer

    def locator(self, selector):
        async def click(**kwargs):
            self.clicks.append((self.url, selector))
        return SimpleNamespace(first=SimpleNamespace(
            scroll_into_view_if_needed=noop, click=click))


class FakeBrowser(BaseUserBrowser):
    def __init__(self, uid=1):
        super().__init__(uid)
        self.started = asyncio.Event()
        self.block_settle = None
        self.in_settle = 0
        self.peak = 0

    async def page(self, key):
        self.active_key = key
        self.started.set()
        return self.pages.setdefault(key, Page())

    async def new_page(self, key):
        self.pages[key] = Page()
        return self.pages[key]

    async def settle(self, page, *args):
        self.in_settle += 1
        self.peak = max(self.peak, self.in_settle)
        try:
            if self.block_settle:
                await self.block_settle.wait()
            await asyncio.sleep(0.01)
        finally:
            self.in_settle -= 1

    async def snapshot(self, page):
        return f"URL : {page.url}"

    async def frame(self, page):
        return ""


def context(cid, run=1, sub=None, uid=1):
    return ToolContext(user={"id": uid}, conversation_id=cid, run_id=run,
                       emit=noop, extra={} if sub is None else {"browser_key": sub})


@pytest.fixture
async def ub(monkeypatch):
    browser = FakeBrowser()
    async def for_user(uid):
        return browser
    monkeypatch.setattr(manager, "for_user", for_user)
    return browser


@pytest.mark.parametrize("sub", [None, "sub-1"])
async def test_two_conversations_keep_their_own_click_target(ub, sub):
    a, b = context(10, sub=sub), context(20, sub=sub)
    await _browser(a, "open", url="https://login.example.test")
    await _browser(b, "open", url="https://mail.example.test")
    first = await _browser(a, "click", ref=5)
    second = await _browser(b, "click", ref=5)
    assert "login.example.test" in first.content
    assert "mail.example.test" in second.content
    assert len(ub.pages) == 2
    assert sorted(p.clicks[0][0] for p in ub.pages.values()) == [
        "https://login.example.test", "https://mail.example.test"]


async def test_next_run_in_same_conversation_keeps_prepared_page(ub):
    await _browser(context(10, run=1), "open", url="https://prepared.example.test")
    result = await _browser(context(10, run=2), "snapshot")
    assert "prepared.example.test" in result.content
    assert len(ub.pages) == 1


async def test_main_and_subagent_have_distinct_pages(ub):
    await _browser(context(10), "open", url="https://main.example.test")
    await _browser(context(10, sub="sub-1"), "open", url="https://sub.example.test")
    assert "main.example.test" in (await _browser(context(10), "snapshot")).content
    assert len(ub.pages) == 2


async def test_missing_conversation_uses_run_scope(ub):
    await _browser(context(0, run=1), "open", url="https://one.example.test")
    await _browser(context(0, run=2), "open", url="https://two.example.test")
    assert "one.example.test" in (await _browser(context(0, run=1), "snapshot")).content
    assert len(ub.pages) == 2


async def test_actions_on_different_pages_of_same_browser_are_serialized(ub):
    await asyncio.gather(*(
        _browser(context(i), "open", url=f"https://page-{i}.example.test")
        for i in range(1, 5)
    ))
    assert ub.peak == 1
    assert len(ub.pages) == 4


async def test_cancelled_action_releases_shared_lock(ub):
    task = asyncio.create_task(_browser(context(10), "wait", seconds=30))
    await ub.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not ub.lock("tool-actions").locked()
    result = await asyncio.wait_for(_browser(context(20), "snapshot"), 1)
    assert "about:blank" in result.content


async def test_action_error_releases_shared_lock(ub, monkeypatch):
    async def fail(page):
        raise RuntimeError("synthetic transport failure")
    monkeypatch.setattr(ub, "snapshot", fail)
    with pytest.raises(RuntimeError, match="synthetic transport failure"):
        await _browser(context(10), "snapshot")
    assert not ub.lock("tool-actions").locked()
    monkeypatch.undo()


async def test_one_users_browser_never_blocks_another(monkeypatch):
    first, second = FakeBrowser(1), FakeBrowser(2)
    gate = first.block_settle = asyncio.Event()
    async def for_user(uid):
        return {1: first, 2: second}[uid]
    monkeypatch.setattr(manager, "for_user", for_user)
    task = asyncio.create_task(_browser(context(10, uid=1), "open", url="https://one.example.test"))
    try:
        await first.started.wait()
        result = await asyncio.wait_for(
            _browser(context(20, uid=2), "open", url="https://two.example.test"), 1)
        assert "two.example.test" in result.content
        assert not task.done()
    finally:
        gate.set()
        await task
