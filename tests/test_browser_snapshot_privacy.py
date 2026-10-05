"""Snapshots réels sur une page locale : aucune session ni donnée personnelle."""
import json
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from ely.browser import SNAPSHOT_JS


@pytest.fixture
async def local_page():
    async with async_playwright() as pw:
        executable = os.environ.get("ELY_BROWSER_EXECUTABLE") or pw.chromium.executable_path
        if not Path(executable).exists():
            pytest.skip("Chromium absent : installer playwright chromium pour ces tests DOM")
        browser = await pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = await browser.new_page()
            yield page
        finally:
            await browser.close()


@pytest.mark.parametrize("attrs", [
    'type="password"',
    'type="text" autocomplete="current-password"',
    'type="text" autocomplete="section-login new-password"',
    'type="text" autocomplete="ONE-TIME-CODE"',
])
async def test_snapshot_masks_filled_secret_without_changing_input(local_page, attrs):
    secret = "synthetic-secret-for-test-7391"
    await local_page.set_content(f'<label for="s">Secret</label><input id="s" {attrs} value="{secret}">')
    snapshot = await local_page.evaluate(SNAPSHOT_JS, 150)
    assert secret not in json.dumps(snapshot)
    assert '[1]' in snapshot["elements"][0]
    assert 'valeur="[masquée]"' in snapshot["elements"][0]
    # Le champ reste utilisable pour se connecter, sans exposer sa valeur.
    assert await local_page.locator('[data-ely-ref="1"]').input_value() == secret


async def test_snapshot_preserves_normal_values_and_controls(local_page):
    await local_page.set_content('''<input aria-label="Recherche" value="recherche locale">
        <textarea aria-label="Message">texte ordinaire</textarea>
        <input type="checkbox" checked aria-label="Case">
        <select aria-label="Choix"><option selected>Premier</option></select>''')
    snapshot = await local_page.evaluate(SNAPSHOT_JS, 150)
    lines = snapshot["elements"]
    assert 'valeur="recherche locale"' in lines[0]
    assert 'valeur="texte ordinaire"' in lines[1]
    assert '☑' in lines[2]
    assert 'options=[*Premier]' in lines[3]
    assert '[masquée]' not in json.dumps(snapshot)


async def test_snapshot_empty_password_remains_actionable(local_page):
    await local_page.set_content('<input type="password" aria-label="Secret" required>')
    snapshot = await local_page.evaluate(SNAPSHOT_JS, 150)
    assert 'champ(password) "Secret"' in snapshot["elements"][0]
    assert '(obligatoire)' in snapshot["elements"][0]
    assert 'valeur=' not in snapshot["elements"][0]
    await local_page.locator('[data-ely-ref="1"]').fill("synthetic-filled-later")
    snapshot = await local_page.evaluate(SNAPSHOT_JS, 150)
    assert 'synthetic-filled-later' not in json.dumps(snapshot)
    assert '[masquée]' in snapshot["elements"][0]


async def test_snapshot_masks_password_in_shadow_root(local_page):
    await local_page.set_content('<div id="host"></div>')
    await local_page.evaluate('''() => {
        const root = document.querySelector('#host').attachShadow({mode: 'open'});
        root.innerHTML = '<input type="password" aria-label="Secret" value="synthetic-shadow-secret">';
    }''')
    snapshot = await local_page.evaluate(SNAPSHOT_JS, 150)
    assert 'synthetic-shadow-secret' not in json.dumps(snapshot)
    assert 'valeur="[masquée]"' in snapshot["elements"][0]
