"""Cycle de vie des onglets : vrais adaptateurs, transports simulés, aucun compte réel."""
from types import SimpleNamespace

import pytest

from ely.browser import UserBrowser, manager
from ely.chrome import ChromeUserBrowser
from ely.tools import ToolContext
from ely.tools.browser import _browser


async def noop(*args, **kwargs):
    pass


class LocalPage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.closed = False

    def on(self, *args):
        pass

    def is_closed(self):
        return self.closed

    async def close(self):
        if not self.context.refuse_close:
            self.closed = True

    async def title(self):
        return self.url

    async def goto(self, url, **kwargs):
        self.url = url

    async def evaluate(self, *args):
        return False

    async def bring_to_front(self):
        pass


class LocalContext:
    def __init__(self):
        self.pages = []
        self.created = 0
        self.refuse_close = False
        self.callback = None

    def on(self, event, callback):
        self.callback = callback

    async def new_page(self):
        page = LocalPage(self)
        self.pages.append(page)
        self.created += 1
        self.callback(page)
        return page


class Bridge:
    user_id = 1

    def __init__(self):
        self.entries = {}
        self.created = 0
        self.refuse_close = False

    async def call(self, command, **kwargs):
        if command == "tabs":
            return [dict(tab_id=i, url=url) for i, url in self.entries.items()]
        if command == "new_tab":
            self.created += 1
            self.entries[self.created] = "about:blank"
            return {"tab_id": self.created}
        i = kwargs["tab_id"]
        if command == "close":
            if not self.refuse_close:
                self.entries.pop(i)
            return True
        if command == "activate":
            return True
        assert command == "cdp", command
        method = kwargs["method"]
        if method == "Page.navigate":
            self.entries[i] = kwargs["params"]["url"]
            return {}
        assert method == "Runtime.evaluate", method
        expression = kwargs["params"]["expression"]
        value = False
        if expression == "document.title":
            value = self.entries[i]
        elif "document.readyState" in expression:
            value = {"s": "complete", "u": self.entries[i]}
        return {"result": {"value": value}}


def ctx(cid=10):
    return ToolContext(user={"id": 1}, conversation_id=cid, run_id=1, emit=noop)


def key(cid=10):
    return f"conversation-{cid}:main"


@pytest.fixture(params=["internal", "chrome"])
async def backend(request, monkeypatch):
    transport = LocalContext() if request.param == "internal" else Bridge()
    ub = UserBrowser(1, transport) if request.param == "internal" else ChromeUserBrowser(transport)
    async def for_user(uid):
        return ub
    async def snapshot(page):
        return page.url
    async def frame(page, **kwargs):
        return ""
    monkeypatch.setattr(manager, "for_user", for_user)
    monkeypatch.setattr(ub, "snapshot", snapshot)
    monkeypatch.setattr(ub, "frame", frame)
    monkeypatch.setattr(ub, "settle", noop)
    return SimpleNamespace(ub=ub, transport=transport)


@pytest.mark.parametrize("action", ["tabs", "close_tab", "switch_tab"])
async def test_empty_browser_actions_never_create_a_tab(backend, action):
    result = await _browser(ctx(), action, tab=0)
    assert backend.transport.created == 0
    assert await backend.ub.list_pages() == []
    assert result.is_error == (action == "switch_tab")


async def test_close_last_tab_and_list_do_not_reopen_it(backend):
    await backend.ub.new_page(key())
    result = await _browser(ctx(), "close_tab")
    assert not result.is_error and "absence vérifiée" in result.content
    assert await backend.ub.list_pages() == []
    assert (await _browser(ctx(), "tabs")).content == "aucun onglet"
    await _browser(ctx(), "close_tab")
    assert backend.transport.created == 1
    assert key() not in backend.ub.pages


async def test_close_preserves_existing_and_other_conversation_tabs(backend):
    await backend.ub.new_page("existing")
    await backend.ub.new_page(key(20))
    await backend.ub.new_page(key())
    await _browser(ctx(), "close_tab")
    assert len(await backend.ub.list_pages()) == 2
    assert "existing" in backend.ub.pages and key(20) in backend.ub.pages
    await _browser(ctx(), "close_tab")
    assert len(await backend.ub.list_pages()) == 2
    assert backend.transport.created == 3


async def test_listing_in_new_conversation_preserves_tab_count(backend):
    await backend.ub.new_page("existing")
    result = await _browser(ctx(), "tabs")
    assert "0:" in result.content and "(actif)" not in result.content
    assert backend.transport.created == 1
    assert key() not in backend.ub.pages


@pytest.mark.parametrize("index", [None, -1, 99])
async def test_invalid_switch_does_not_change_or_create_pages(backend, index):
    original = await backend.ub.new_page(key())
    result = await _browser(ctx(), "switch_tab", tab=index)
    assert result.is_error
    assert backend.ub.pages[key()] is original
    assert backend.transport.created == 1


async def test_switch_without_current_page_reuses_selected_tab(backend):
    await backend.ub.new_page("existing")
    result = await _browser(ctx(), "switch_tab", tab=0)
    assert not result.is_error
    assert backend.transport.created == 1
    assert key() in backend.ub.pages
    assert "(actif)" in (await _browser(ctx(), "tabs")).content


async def test_close_is_not_claimed_when_tab_remains(backend):
    await backend.ub.new_page(key())
    backend.transport.refuse_close = True
    result = await _browser(ctx(), "close_tab")
    assert result.is_error and "Fermeture non confirmée" in result.content
    assert len(await backend.ub.list_pages()) == 1
    assert backend.transport.created == 1
    backend.transport.refuse_close = False
    assert not (await _browser(ctx(), "close_tab")).is_error
    assert await backend.ub.list_pages() == []


async def test_already_closed_target_does_not_close_another_tab(backend):
    target = await backend.ub.new_page(key())
    await backend.ub.new_page(key(20))
    await target.close()  # fermeture externe avant le prochain appel d'outil
    result = await _browser(ctx(), "close_tab")
    assert "Aucun onglet courant" in result.content
    assert len(await backend.ub.list_pages()) == 1
    assert backend.transport.created == 2


async def test_first_open_new_tab_creates_exactly_one_page(backend):
    result = await _browser(ctx(), "open", new_tab=True, url="https://example.test")
    assert not result.is_error
    assert backend.transport.created == 1
    assert len(await backend.ub.list_pages()) == 1


async def test_open_without_url_does_not_create_a_page(backend):
    result = await _browser(ctx(), "open", new_tab=True)
    assert result.is_error
    assert backend.transport.created == 0
