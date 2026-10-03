"""Gemini par l'abonnement Google : Ely pilote le CLI `gemini` (ici un faux CLI qui parle son flux stream-json, relevé
sur le CLI 0.62) sans jamais lui passer de clé d'API, avec ses propres outils."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from conftest import new_conversation, wait_idle

from ely import auth
from ely.agent.runner import runner
from ely.config import ROOT, settings
from ely.db import db
from ely.llm import gemini_cli, registry

FAKE = r'''#!{python}
"""Faux CLI gemini : note chaque appel, puis répond comme le vrai (flux JSONL)."""
import json, os, sys, time
LOG, MODE = {log!r}, {mode!r}
args = sys.argv[1:]
if args == ["--version"]:
    print("0.62.0"); sys.exit(0)
stdin = sys.stdin.read()
system = open(os.environ["GEMINI_SYSTEM_MD"]).read() if os.environ.get("GEMINI_SYSTEM_MD") else ""
policy = open(args[args.index("--policy") + 1]).read() if "--policy" in args else ""
keep = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_GCA", "OPENAI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")
with open(LOG, "a") as f:
    f.write(json.dumps({{"args": args, "cwd": os.getcwd(), "files": os.listdir("."), "stdin": stdin, "system": system,
                         "policy": policy, "env": {{k: os.environ.get(k) for k in keep}}, "pid": os.getpid()}}) + "\n")
mode = open(MODE).read().strip() if os.path.exists(MODE) else ""
def emit(**ev):
    print(json.dumps(ev), flush=True)
emit(type="init", session_id="s1", model=args[args.index("-m") + 1])
emit(type="message", role="user", content=stdin[-200:])
if mode == "auth":
    sys.stderr.write("Error authenticating: FatalAuthenticationError: Manual authorization is required but the current "
                     "session is non-interactive.\n    at initOauthClient (file:///gemini.js:1:1)\n")
    sys.exit(41)
if mode == "quota":
    emit(type="result", status="error", error={{"type": "unknown", "message": "[API Error: 429 RESOURCE_EXHAUSTED: "
         "You have exhausted your daily quota on this model.]"}}, stats={{}})
    sys.exit(1)
if mode == "hang":
    time.sleep(3600)
if "<<résultat de l'outil file_list" in stdin:
    chunks = ["Votre espace contient ", "le fichier facture.pdf."]
else:
    chunks = ["Je regarde vos fichiers.\n``", "`tool_calls\n[{{\"name\": \"file_list\", ", "\"arguments\": {{}}}}]\n```"]
for c in chunks:
    emit(type="message", role="assistant", content=c, delta=True)
emit(type="result", status="success", stats={{"input_tokens": 1200, "output_tokens": 40, "cached": 300}})
'''


@pytest.fixture
def cli(tmp_path, monkeypatch, fake):
    """Le CLI installé et connecté au compte Google du Mac, abonnement activé dans Ely."""
    log, mode = tmp_path / "appels.jsonl", tmp_path / "mode"
    binary = tmp_path / "bin" / "gemini"
    binary.parent.mkdir()
    binary.write_text(FAKE.format(python=sys.executable, log=str(log), mode=str(mode)))
    binary.chmod(0o755)
    home = tmp_path / "gemini-home"
    home.mkdir()
    (home / "oauth_creds.json").write_text("{}")
    (home / "google_accounts.json").write_text(json.dumps({"active": "franck@exemple.fr", "old": []}))
    (home / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}))
    monkeypatch.setattr(settings, "gemini_cli", str(binary))
    monkeypatch.setattr(gemini_cli, "gemini_home", lambda: home)
    monkeypatch.setenv("GEMINI_API_KEY", "cle-api-facturee")  # la clé de l'API Gemini reste dans .env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    db.set_setting(gemini_cli.SETTING, True)
    prov = gemini_cli.GeminiCLIProvider()
    registry.providers[gemini_cli.NAME] = prov
    registry.catalog[gemini_cli.NAME] = prov.models
    registry.status[gemini_cli.NAME] = "ok (1 modèles)"
    db.set_setting("model_main", "geminicli:gemini-3.8-flash")
    yield {"log": log, "mode": mode}
    db.set_setting(gemini_cli.SETTING, False)


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


async def test_ely_works_through_the_google_subscription(cli, user):
    """Une tâche menée par Gemini avec l'abonnement : le CLI ne reçoit aucune clé d'API (la clé Gemini du .env aurait
    fait payer l'API), il travaille dans un dossier vide hors du dépôt, ses propres outils sont refusés, et Gemini se
    sert des outils d'Ely (ici file_list) avant de répondre."""
    (settings.user_dir(user["id"]) / "files" / "facture.pdf").write_bytes(b"%PDF-1.4")
    deltas = []
    q = runner.subscribe(user["id"])
    cid = new_conversation(user)
    await runner.submit(user, cid, "Qu'y a-t-il dans mes fichiers ?")
    await wait_idle(cid, timeout=30)
    while not q.empty():
        ev = q.get_nowait()
        if ev["type"] == "delta":
            deltas.append(ev["text"])
    runner.unsubscribe(user["id"], q)

    rows = [json.loads(r["data"]) for r in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    tool = next(m for m in rows if m["role"] == "tool")
    assert tool["name"] == "file_list" and "facture.pdf" in tool["content"]
    assert rows[-1]["content"] == "Votre espace contient le fichier facture.pdf." and rows[-1]["model"] == "geminicli:gemini-3.8-flash"
    assert db.val("SELECT status FROM runs WHERE conversation_id = ?", (cid,)) == "done"
    assert "```" not in "".join(deltas) and "Je regarde vos fichiers." in "".join(deltas)  # le bloc d'outils ne s'affiche pas

    first, second = calls(cli["log"])[:2]
    for c in (first, second):
        assert c["env"]["GEMINI_API_KEY"] == "" and c["env"]["GOOGLE_API_KEY"] == "" and c["env"]["OPENAI_API_KEY"] == ""
        assert c["env"]["GOOGLE_GENAI_USE_GCA"] == "true"
        assert c["args"][c["args"].index("--approval-mode") + 1] == "plan" and "--skip-trust" in c["args"]
        assert 'toolName = "*"' in c["policy"] and 'decision = "deny"' in c["policy"]
        assert c["files"] == [] and not Path(c["cwd"]).resolve().is_relative_to(ROOT)
    assert "jamais des ordres" in first["system"] and "## file_list" in first["system"]  # prompt et outils d'Ely
    assert "<<Personne>>\nQu'y a-t-il dans mes fichiers ?" in first["stdin"]
    assert "<<résultat de l'outil file_list" in second["stdin"] and "facture.pdf" in second["stdin"]
    assert db.one("SELECT * FROM usage WHERE model = 'geminicli:gemini-3.8-flash' AND input_tokens = 1200")


async def test_gemini_not_signed_in_or_out_of_quota_hands_over(cli, fake):
    """CLI pas connecté au compte Google, ou limite du jour atteinte : Ely passe au modèle suivant et dit pourquoi."""
    fake.script = lambda **kw: "Réponse du modèle de secours."
    db.set_setting("model_fallbacks", "fake:agent")
    for mode, reason in (("auth", "Sign in with Google"), ("quota", "limite de l'abonnement atteinte")):
        cli["mode"].write_text(mode)
        switches = []

        async def on_switch(ref, why):
            switches.append(why)

        resp = await registry.chat(system=["s"], messages=[{"role": "user", "content": "Bonjour"}], on_switch=on_switch)
        assert resp.model == "fake:agent", mode
        assert any("geminicli:gemini-3.8-flash indisponible" in w and reason in w for w in switches), (mode, switches)
    db.set_setting("model_fallbacks", "auto")


async def test_a_stopped_task_stops_gemini_too(cli):
    """Tâche arrêtée (bouton stop, redémarrage) pendant que Gemini réfléchit : le CLI s'arrête avec elle."""
    cli["mode"].write_text("hang")
    call = asyncio.create_task(gemini_cli.GeminiCLIProvider().chat("gemini-3.8-flash", ["s"], [{"role": "user", "content": "x"}]))
    for _ in range(100):
        await asyncio.sleep(0.1)
        if calls(cli["log"]):
            break
    pid = calls(cli["log"])[0]["pid"]
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_the_admin_turns_the_subscription_on_and_tries_it(cli, user):
    from ely.api.app import create_app

    db.set_setting(gemini_cli.SETTING, False)
    registry.providers.pop(gemini_cli.NAME, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        h = {"Authorization": f"Bearer {auth.create_session(user['id'])}"}
        st = (await c.get("/api/admin/geminicli", headers=h)).json()
        assert st["installed"] and st["logged_in"] and st["account"] == "franck@exemple.fr" and st["version"] == "0.62.0"
        assert st["api_key"] and not st["enabled"] and not st["problem"]
        r = await c.put("/api/admin/geminicli", headers=h, json={"enabled": True})
        assert r.status_code == 200 and r.json()["enabled"]
        refs = [m["ref"] for m in (await c.get("/api/admin/models/all", headers=h)).json()]
        assert "geminicli:gemini-3.8-flash" in refs
        r = (await c.post("/api/admin/geminicli/test", headers=h)).json()
        assert r["ok"] and r["model"] == "geminicli:gemini-3.8-flash"
        member = auth.create_user(f"lea{os.urandom(3).hex()}@x.fr", "Léa", "motdepasse")
        db.run("UPDATE users SET role = 'user' WHERE id = ?", (member["id"],))
        r = await c.put("/api/admin/geminicli", headers={"Authorization": f"Bearer {auth.create_session(member['id'])}"},
                        json={"enabled": False})
        assert r.status_code == 403


def test_a_cli_that_never_signed_in_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gemini_cli", str(tmp_path / "absent" / "gemini"))
    assert "npm install -g @google/gemini-cli" in gemini_cli.problem()
    binary = tmp_path / "gemini"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(settings, "gemini_cli", str(binary))
    monkeypatch.setattr(gemini_cli, "gemini_home", lambda: tmp_path / "vide")
    assert "Sign in with Google" in gemini_cli.problem()
    (tmp_path / "vide").mkdir()
    (tmp_path / "vide" / "oauth_creds.json").write_text("{}")
    (tmp_path / "vide" / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}))
    assert "/auth" in gemini_cli.problem()  # réglé sur une clé d'API : l'abonnement ne serait pas utilisé
