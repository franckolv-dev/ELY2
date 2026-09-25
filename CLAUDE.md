# Ely 2 — notes pour le développement

Agent personnel autonome : FastAPI + SQLite + interface PWA sans compilation. Tout est en français (interface, commentaires, messages).

## Commandes
- Lancer : `./ely.sh` (superviseur : redémarrage + retour arrière après auto-mise à jour) ou `python -m ely`
- Tests : `.venv/bin/python -m pytest -q` (modèle simulé, aucun appel réseau réel ; Chromium requis pour le test navigateur)
- Interface sans tokens : `python scripts/mock_llm.py` + `CUSTOM_OPENAI_BASE_URL=http://127.0.0.1:9100/v1 ELY_MODEL_MAIN=custom:mock-agent`
- Parcours UI complet : `python scripts/e2e_ui.py http://127.0.0.1:8000 captures/`

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
- Pas de configuration éclatée : secrets dans `.env`, choix de l'admin dans `app_settings`, rien en cache mémoire qui masquerait un réglage.
