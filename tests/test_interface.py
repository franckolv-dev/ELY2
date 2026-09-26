"""Interface dans un vrai navigateur (Chromium) : comportements qui ne se vérifient que côté page."""
from __future__ import annotations

import asyncio
import os
import socket

import pytest
import uvicorn

from ely import auth
from ely.api.app import create_app

pytestmark = pytest.mark.skipif(not os.environ.get("ELY_BROWSER_EXECUTABLE"), reason="Chromium absent")

# voix telles que Chrome les présente sur un Mac : voix système, voix « fantaisie » en France et au Canada, voix Google
MAC_VOICES = """
  const V = (name, lang) => ({ name, lang, voiceURI: name, localService: true, default: false });
  const voices = [V("Shelley (français (France))", "fr-FR"), V("Grandpa (français (Canada))", "fr-CA"),
    V("Rocko (français (France))", "fr-FR"), V("Thomas", "fr-FR"), V("Amélie", "fr-CA"),
    V("Google français", "fr-FR"), V("Samantha", "en-US")];
  Object.defineProperty(speechSynthesis, "getVoices", { value: () => voices });
  window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  window.spoken = [];
  Object.defineProperty(speechSynthesis, "speak", { value: (u) => window.spoken.push([u.voice && u.voice.name, u.text]) });
  Object.defineProperty(speechSynthesis, "cancel", { value: () => {} });
"""


@pytest.fixture
async def ely_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def test_voice_choice_leaves_out_robotic_voices_and_can_be_tried(ely_url, user):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ["ELY_BROWSER_EXECUTABLE"])
        ctx = await browser.new_context(locale="fr-FR")
        await ctx.add_cookies([{"name": "ely_token", "value": auth.create_session(user["id"]), "url": ely_url}])
        # la voix choisie avant la mise à jour était l'une des voix robotiques
        await ctx.add_init_script(MAC_VOICES + "localStorage.setItem('ely-voice', 'Shelley (français (France))');")
        page = await ctx.new_page()
        await page.goto(ely_url)
        await page.click(".settings-btn")
        voices = await page.locator("select[aria-label='Voix'] option").all_inner_texts()
        assert voices == ["Automatique", "Google français", "Amélie", "Thomas"], voices
        # « Tester » : la meilleure voix, sur la phrase tapée par l'utilisateur
        await page.fill(".voice-test input", "Votre train part à 8 h 12.")
        await page.click(".voice-test button")
        assert await page.evaluate("window.spoken") == [["Google français", "Votre train part à 8 h 12."]]
        # une voix choisie dans la liste est bien celle qu'on entend
        await page.select_option("select[aria-label='Voix']", "Thomas")
        await page.click(".voice-test button")
        assert (await page.evaluate("window.spoken"))[-1][0] == "Thomas"
        await browser.close()


# lecteur audio factice : note chaque extrait joué et le termine aussitôt
AUDIO = """
  window.played = 0;
  window.Audio = class { constructor(src) { this.src = src; }
    play() { window.played++; setTimeout(() => this.onended && this.onended(), 5); return Promise.resolve(); }
    pause() {} };
"""


async def test_recorded_voice_reads_sentence_by_sentence_and_falls_back(ely_url, user, xtts):
    """La voix clonée du Mac est proposée en premier et lit phrase par phrase ; si son service tombe,
    la lecture continue avec la meilleure voix du navigateur."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ["ELY_BROWSER_EXECUTABLE"])
        ctx = await browser.new_context(locale="fr-FR")
        await ctx.add_cookies([{"name": "ely_token", "value": auth.create_session(user["id"]), "url": ely_url}])
        await ctx.add_init_script(MAC_VOICES + AUDIO)
        page = await ctx.new_page()
        await page.goto(ely_url)
        await page.click(".settings-btn")
        await page.wait_for_selector("select[aria-label='Voix'] optgroup", state="attached")
        groups = await page.locator("select[aria-label='Voix'] optgroup").evaluate_all("gs => gs.map(g => g.label)")
        assert groups == ["Voix enregistrées (sur le Mac)", "Voix du navigateur"], groups
        assert await page.locator("select[aria-label='Voix'] option[value='xtts:gert']").inner_text() == "Gert"
        # « Automatique » : la voix enregistrée, une requête par phrase
        await page.fill(".voice-test input", "Votre train part à 8 h 12 de la gare de Lyon. Pensez à prendre votre billet sur l'application.")
        await page.click(".voice-test button")
        await page.wait_for_function("window.played === 2")
        assert [s["text"] for s in xtts] == ["Votre train part à 8 h 12 de la gare de Lyon.", "Pensez à prendre votre billet sur l'application."]
        assert all(s["voice"] == "gert" for s in xtts) and await page.evaluate("window.spoken") == []
        # le service vocal échoue : repli sur la voix du navigateur, sans silence
        await page.fill(".voice-test input", "Le service vocal est en panne ce matin, désolée.")
        await page.click(".voice-test button")
        await page.wait_for_function("window.spoken.length === 1")
        assert await page.evaluate("window.spoken") == [["Google français", "Le service vocal est en panne ce matin, désolée."]]
        await browser.close()
