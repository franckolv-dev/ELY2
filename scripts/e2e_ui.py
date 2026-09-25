"""Parcours de bout en bout de l'interface (Playwright) contre un Ely branché sur scripts/mock_llm.py.

    python scripts/e2e_ui.py http://127.0.0.1:8765 dossier_captures/
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "captures")
OUT.mkdir(parents=True, exist_ok=True)
EXE = os.environ.get("ELY_BROWSER_EXECUTABLE") or None


async def main() -> None:
    errors: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=EXE)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="fr-FR", color_scheme="light")
        await ctx.add_init_script("localStorage.setItem('ely-push-dismissed', '1')")
        page = await ctx.new_page()
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        await page.goto(BASE)
        await page.wait_for_selector("text=Bienvenue dans Ely")
        await page.screenshot(path=OUT / "01-creation-compte.png")
        await page.fill("input[autocomplete=given-name]", "Franck")
        await page.fill("input[type=email]", "franck@exemple.fr")
        await page.fill("input[type=password]", "motdepasse")
        await page.click("button:has-text('Créer mon compte')")
        await page.wait_for_selector("text=que puis-je faire pour toi")
        await page.screenshot(path=OUT / "02-accueil.png")

        # 1) une action simple
        await page.fill(".composer textarea", "Ajoute Jean Dupont à mes contacts, c'est mon plombier : 06 12 34 56 78")
        await page.keyboard.press("Enter")
        await page.wait_for_selector("text=est dans tes contacts", timeout=20000)
        await page.wait_for_timeout(1200)
        await page.screenshot(path=OUT / "03-contact.png")

        # 2) une vraie démarche dans le navigateur
        await page.click("button:has-text('Nouvelle demande')")
        await page.fill(".composer textarea", "Prends-moi rendez-vous chez mon médecin traitant cette semaine, en fin de journée")
        await page.keyboard.press("Enter")
        await page.wait_for_selector(".steps", timeout=20000)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=OUT / "04-rdv-en-cours.png")
        await page.wait_for_selector("text=Rendez-vous réservé", timeout=40000)
        await page.wait_for_timeout(1500)
        await page.click(".steps-head")
        await page.wait_for_timeout(300)
        await page.screenshot(path=OUT / "05-rdv-termine.png")

        # 3) une question de l'agent
        await page.click("button:has-text('Nouvelle demande')")
        await page.fill(".composer textarea", "Pose-moi une question de test")
        await page.keyboard.press("Enter")
        await page.wait_for_selector(".ask-card", timeout=20000)
        await page.screenshot(path=OUT / "06-question.png")
        await page.click(".ask-card button:has-text('123456')")
        await page.wait_for_selector("text=Code bien reçu", timeout=20000)

        # 4) réglages
        await page.wait_for_timeout(1500)
        await page.click("button[title=Réglages]")
        await page.click(".sheet nav button:has-text('Mémoire')")
        await page.wait_for_selector("text=Ce qu'Ely sait de toi")
        await page.wait_for_timeout(500)
        await page.screenshot(path=OUT / "07-memoire.png")
        await page.click(".sheet nav button:has-text('Modèles')")
        await page.wait_for_selector("text=Agent principal")
        await page.screenshot(path=OUT / "08-modeles.png")
        await page.click(".sheet nav button:has-text('Auto-amélioration')")
        await page.wait_for_selector("text=Journal des améliorations")
        await page.screenshot(path=OUT / "09-auto-amelioration.png")
        await page.click(".sheet nav button:has-text('Connexions')")
        await page.wait_for_selector("text=Boîte mail")
        await page.screenshot(path=OUT / "10-connexions.png")
        await page.keyboard.press("Escape")

        # 5) sombre
        dark = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="fr-FR", color_scheme="dark",
                                         storage_state=await ctx.storage_state())
        await dark.add_init_script("localStorage.setItem('ely-push-dismissed', '1')")
        dpage = await dark.new_page()
        await dpage.goto(BASE)
        await dpage.wait_for_selector(".conv")
        await dpage.click(".conv >> nth=1")
        await dpage.wait_for_selector("text=Rendez-vous réservé")
        await dpage.wait_for_timeout(800)
        await dpage.screenshot(path=OUT / "11-sombre.png")

        # 6) téléphone Android
        phone = await browser.new_context(**p.devices["Pixel 7"], locale="fr-FR", storage_state=await ctx.storage_state())
        await phone.add_init_script("localStorage.setItem('ely-push-dismissed', '1')")
        m = await phone.new_page()
        m.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
        await m.goto(BASE)
        await m.wait_for_selector("text=que puis-je faire pour toi")
        await m.screenshot(path=OUT / "12-mobile-accueil.png")
        await m.click(".menu-btn")
        await m.wait_for_timeout(400)
        await m.screenshot(path=OUT / "13-mobile-conversations.png")
        await m.click(".conv >> nth=1")
        await m.wait_for_selector("text=Rendez-vous réservé")
        await m.wait_for_timeout(800)
        await m.screenshot(path=OUT / "14-mobile-rdv.png")
        await m.fill(".composer textarea", "Compare le train et la voiture pour Lyon–Paris")
        await m.click("button.send[title=Envoyer]")
        await m.wait_for_selector("text=Je te conseille", timeout=20000)
        await m.wait_for_timeout(800)
        await m.screenshot(path=OUT / "15-mobile-reponse.png")
        await browser.close()

    print("ERREURS :", *errors, sep="\n  ") if errors else print("Aucune erreur JavaScript.")


asyncio.run(main())
