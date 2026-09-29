# Ely — notes pour le développement

Agent personnel autonome : FastAPI + SQLite + interface PWA sans compilation. Code, commentaires et messages du serveur en français ;
l'interface est bilingue (français vouvoyé / anglais) : tout texte affiché passe par `t()` de `ely/web/js/i18n.js`, dans les deux langues.

## Commandes
- Lancer : `./ely.sh` (superviseur : redémarrage + retour arrière après auto-mise à jour) ou `python -m ely`
- Tests : `.venv/bin/python -m pytest -q` (modèle simulé, aucun appel réseau réel ; tests navigateur et extension ignorés sans `ELY_BROWSER_EXECUTABLE`, par exemple le Chromium de Playwright : `~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`)
- Interface sans tokens : `python scripts/mock_llm.py` + `CUSTOM_OPENAI_BASE_URL=http://127.0.0.1:9100/v1 ELY_MODEL_MAIN=custom:mock-agent`
- Parcours UI complet : `python scripts/e2e_ui.py http://127.0.0.1:8000 captures/`
- Publier une version : section dans `CHANGELOG.md`, `__version__` (`ely/__init__.py`) et `pyproject.toml`, puis `git tag vX.Y.Z && git push origin vX.Y.Z` sur `main` : le workflow `Release` publie la release avec les notes du CHANGELOG

## Repères
- Boucle d'agent : `ely/agent/loop.py` (contrôleur d'objectif `verify`, compaction, sous-agents) ; tâches de fond et flux SSE : `ely/agent/runner.py`
- Modèles : `ely/llm/registry.py` (rôles main/strong/fast/local/embed, choix auto, repli) ; réglages admin en base, relus à chaque appel
- Navigateur : `ely/chrome.py` (Chrome de l'utilisateur via l'extension `extension/`, prioritaire) et `ely/browser.py` (interne, secours) ; l'outil `browser` ne voit que l'interface commune
- Outils : décorateur `@tool` dans `ely/tools/__init__.py` ; un outil renvoie `ToolResult`, ne lève jamais vers le modèle
- Garder la liste d'outils courte et les descriptions concises (coût en tokens à chaque tour)
- Format de message canonique : voir l'en-tête de `ely/llm/base.py`
- Auto-amélioration : `ely/selfdev/` (plugins à chaud dans `data/plugins/`, modifications du code via worktree + tests + `ely.sh`)

## Règles
- Toute correction s'accompagne d'un test de comportement (pas de test qui lit le code source).
- Ne jamais rejouer automatiquement une action dont le résultat est incertain (voir `LOST` dans loop.py).
- Vouvoiement de rigueur (interface, Ely, messages du serveur) ; tutoiement seulement si la personne le demande (`auth.tv`, réglage `address`).
- Pas de configuration éclatée : secrets dans `.env`, choix de l'admin dans `app_settings`, rien en cache mémoire qui masquerait un réglage.
- Extension `extension/` : suivre la compétence `chrome-extensions` de Modern Web Guidance (service worker éphémère : état dans `chrome.storage`, minuteries en `chrome.alarms`, `async/await`) et tenir à jour `extension/CHROMEWEBSTORE.md` (justification de chaque permission, données, historique des versions) à chaque modification.
