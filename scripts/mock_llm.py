"""Faux serveur de modèle compatible OpenAI, pour essayer l'interface sans dépenser de tokens.

    python scripts/mock_llm.py            # écoute sur http://127.0.0.1:9100/v1
    CUSTOM_OPENAI_BASE_URL=http://127.0.0.1:9100/v1 ELY_MODEL_MAIN=custom:mock-agent ./ely.sh

Il simule un agent qui appelle de vrais outils d'Ely selon des mots-clés :
« contact », « rendez-vous »/« page », « question », sinon une réponse rédigée.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI()
PORT = int(os.environ.get("MOCK_PORT", 9100))

DEMO = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Cabinet du Dr Martin — Prise de rendez-vous</title>
<style>body{font-family:system-ui;margin:0;background:#f4f6fb;color:#1b2a4a}header{background:#107aca;color:#fff;padding:18px 28px;font-size:20px;font-weight:700}
main{max-width:760px;margin:24px auto;background:#fff;border-radius:14px;padding:26px;box-shadow:0 4px 20px rgba(0,0,0,.08)}
.slots{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.slot{border:1px solid #107aca;color:#107aca;background:#fff;border-radius:8px;padding:10px 14px;cursor:pointer;font-weight:600}
label{display:block;margin:12px 0 4px;font-weight:600}input,select{width:100%;padding:10px;border:1px solid #c9d3e6;border-radius:8px;font-size:15px}
button.go{margin-top:18px;background:#107aca;color:#fff;border:0;border-radius:8px;padding:12px 20px;font-size:16px;font-weight:700;cursor:pointer}</style></head>
<body><header>🩺 Doctolib-démo</header><main><h1>Dr Claire Martin — Médecin généraliste</h1><p>12 rue de la République, 69002 Lyon</p>
<h3>Prochaines disponibilités</h3><div class="slots"><button class="slot">Jeudi 2 oct. 17:30</button><button class="slot">Jeudi 2 oct. 18:00</button><button class="slot">Vendredi 3 oct. 17:45</button></div>
<label for="nom">Nom du patient</label><input id="nom" placeholder="Nom et prénom"><label for="motif">Motif</label>
<select id="motif"><option>Consultation</option><option>Renouvellement d'ordonnance</option></select>
<button class="go" onclick="document.querySelector('main').innerHTML='<h1>✅ Rendez-vous confirmé</h1><p>Jeudi 2 octobre à 17:30 avec le Dr Martin pour '+document.getElementById('nom').value+'.</p>'">Confirmer le rendez-vous</button>
</main></body></html>"""


def text_of(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


def decide(messages: list[dict]) -> tuple[str, list[dict]]:
    last_user_i = max(i for i, m in enumerate(messages) if m["role"] == "user" and not text_of(m).startswith("[Images"))
    ask = text_of(messages[last_user_i])
    if "contrôleur qualité" in ask:
        return json.dumps({"done": True, "missing": ""}), []
    if "Donne un titre" in ask:
        if "contact" in ask.lower():
            return "Ajout d'un contact", []
        if "rendez-vous" in ask.lower():
            return "Rendez-vous chez le médecin", []
        return "Discussion avec Ely", []
    if "mémoire d'Ely" in ask:
        return json.dumps({"profile": "## Identité\n- Prénom : Franck\n## Préférences\n- Rendez-vous médicaux en fin de journée",
                           "facts": ["Le médecin traitant de Franck est le Dr Claire Martin à Lyon"], "skill": None}), []
    # dernière vraie demande de l'utilisateur (hors contrôles)
    first_user = max(i for i, m in enumerate(messages) if m["role"] == "user" and not text_of(m).startswith(("[Contrôle", "[Images")))
    request = text_of(messages[first_user]).lower()
    tools_done = [m for m in messages[first_user:] if m["role"] == "tool"]
    n = len(tools_done)
    if "contact" in request:
        if n == 0:
            return "", [{"name": "contacts_save", "arguments": {"name": "Jean Dupont", "phone": "06 12 34 56 78", "company": "Atelier Dupont"}}]
        return "✅ C'est fait : **Jean Dupont** (Atelier Dupont, 06 12 34 56 78) est dans vos contacts.", []
    if "rendez-vous" in request or "page" in request:
        if n == 0:
            return "Je cherche un créneau en fin de journée chez votre médecin.", [
                {"name": "recall", "arguments": {"query": "médecin traitant"}},
                {"name": "browser", "arguments": {"action": "open", "url": f"http://127.0.0.1:{PORT}/demo"}}]
        last = tools_done[-1]["content"]
        if n == 2:
            ref = re.search(r"\[(\d+)\] champ\(text\) \"Nom", last)
            return "", [{"name": "browser", "arguments": {"action": "type", "ref": int(ref.group(1)) if ref else 1, "text": "Franck Olivier"}}]
        if n == 3:
            ref = re.search(r"\[(\d+)\] bouton \"Confirmer", last)
            return "", [{"name": "browser", "arguments": {"action": "click", "ref": int(ref.group(1)) if ref else 1}}]
        if n == 4:
            return "", [{"name": "calendar_add", "arguments": {"title": "Dr Claire Martin (généraliste)", "start": "2030-10-02T17:30",
                                                               "location": "12 rue de la République, 69002 Lyon", "reminder_minutes": 60}}]
        return ("✅ **Rendez-vous réservé** avec le Dr Claire Martin\n\n- 📅 **Jeudi 2 octobre à 17 h 30**\n"
                "- 📍 12 rue de la République, 69002 Lyon\n- 🔔 Ajouté à votre agenda avec un rappel 1 h avant"), []
    if "question" in request:
        if n == 0:
            return "", [{"name": "ask_user", "arguments": {"question": "Quel code avez-vous reçu par SMS ?", "options": ["123456", "Je n'ai rien reçu"]}}]
        return f"Merci ! Code bien reçu : {tools_done[-1]['content'].split(':')[-1].strip()}.", []
    return ("Voici un résumé clair :\n\n| Option | Prix | Délai |\n|---|---|---|\n| Train | 45 € | 2 h |\n| Voiture | 60 € | 4 h |\n\n"
            "Je vous conseille **le train** : plus rapide et moins cher. Voulez-vous que je réserve ?"), []


@app.get("/v1/models")
def models():
    return {"data": [{"id": "mock-agent"}]}


@app.get("/demo", response_class=HTMLResponse)
def demo():
    return DEMO


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    text, calls = decide(body["messages"])

    async def stream():
        for i in range(0, len(text), 6):
            yield f"data: {json.dumps({'choices': [{'delta': {'content': text[i:i + 6]}}]})}\n\n"
            await asyncio.sleep(0.02)
        for k, c in enumerate(calls):
            await asyncio.sleep(0.3)
            yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": k, "id": f"call_{os.urandom(4).hex()}",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}]}}]}) + "\n\n"
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if calls else 'stop'}], 'usage': {'prompt_tokens': 900, 'completion_tokens': 60}})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
