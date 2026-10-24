"""Navigateur interne : Chromium de Playwright absent (mise à jour sans « playwright install ») -> Google Chrome."""
import pytest

from ely import browser
from ely.config import settings

MISSING = ("BrowserType.launch: Executable doesn't exist at /Users/x/Library/Caches/ms-playwright/"
           "chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell")


class FakeChromium:
    def __init__(self, chrome_installed=True):
        self.calls = []
        self.chrome_installed = chrome_installed

    async def launch(self, **kw):
        self.calls.append(kw)
        if kw.get("channel") == "chrome" and self.chrome_installed:
            return "chrome"
        if kw.get("channel") == "chrome":
            raise RuntimeError("Chromium distribution 'chrome' is not found")
        raise RuntimeError(MISSING)


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(settings, "browser_executable", "")
    monkeypatch.setattr(settings, "browser_channel", "")
    return browser.BrowserManager()


def use(monkeypatch, manager, chromium):
    async def playwright():
        return FakePlaywright(chromium)
    monkeypatch.setattr(manager, "_playwright", playwright)


async def test_missing_playwright_chromium_falls_back_to_installed_chrome(monkeypatch, manager):
    chromium = FakeChromium()
    use(monkeypatch, manager, chromium)
    assert await manager.shared() == "chrome"
    assert [c.get("channel") for c in chromium.calls] == [None, "chrome"]


async def test_without_chrome_the_original_error_is_kept(monkeypatch, manager):
    chromium = FakeChromium(chrome_installed=False)
    use(monkeypatch, manager, chromium)
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        await manager.shared()


async def test_configured_browser_is_never_replaced(monkeypatch, manager):
    chromium = FakeChromium()
    with pytest.raises(RuntimeError, match="Executable doesn't exist"):
        await manager._launch(chromium.launch, headless=True, executable_path="/opt/chromium")
    assert len(chromium.calls) == 1
