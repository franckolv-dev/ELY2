"""Stabilité d'Ely dans la durée : action au résultat incertain jamais présentée comme un échec, modèles en panne ou
trop courts contournés, LM Studio lancé après Ely, historique allégé de ses images."""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager

import pytest
import uvicorn
from conftest import new_conversation, wait_idle
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ely import integrations
from ely.agent.runner import runner, save_message
from ely.db import db
from ely.llm import LLMError, registry
from ely.llm.base import estimate_tokens
from ely.tools import TOOLS, ToolContext, execute

registry_module = sys.modules["ely.llm.registry"]  # le paquet expose l'instance sous le même nom que le module


async def _noop(*a, **k):
    pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def tool_results(cid: int) -> list[dict]:
    return [json.loads(r["data"]) for r in db.all("SELECT data FROM messages WHERE conversation_id = ? AND role = 'tool'", (cid,))]


def last_answer(cid: int) -> dict:
    row = db.one("SELECT data FROM messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (cid,))
    return json.loads(row["data"])


# ---------------------------------------------------------------------- action au résultat incertain
async def test_a_mail_sent_too_slowly_is_never_reported_as_failed(user, monkeypatch):
    """Le serveur d'envoi met plus longtemps que prévu : le mail part quand même. Ely ne dit pas « échec, essaie
    autrement » (le modèle le renverrait) mais « peut-être envoyé, vérifie avant de refaire »."""
    from ely.integrations import mail

    sent = []

    def slow_smtp(c, msg):
        time.sleep(0.6)
        sent.append(msg["To"])

    integrations.put(user["id"], "email", {"address": "franck@exemple.fr", "password": "x", **mail.preset_for("exemple.fr")})
    monkeypatch.setattr(mail, "_send_sync", slow_smtp)
    monkeypatch.setattr(TOOLS["email_send"], "timeout", 0.2)
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    r = await execute(ctx, "email_send", {"to": "traiteur@exemple.fr", "subject": "Commande", "body": "Bonjour"})
    assert r.is_error and "Résultat incertain" in r.content and "vérifie" in r.content and "Essaie autrement" not in r.content
    await asyncio.sleep(0.8)
    assert sent == ["traiteur@exemple.fr"]  # il est bien parti : le refaire aurait envoyé un doublon

    monkeypatch.setattr(mail, "_search_sync", lambda *a: time.sleep(0.6) or [])
    monkeypatch.setattr(TOOLS["email_search"], "timeout", 0.2)
    r = await execute(ctx, "email_search", {"query": "Commande"})  # une simple lecture peut être relancée sans risque
    assert r.is_error and "Essaie autrement" in r.content and "incertain" not in r.content


class _LostLocator:
    def __init__(self, clicks: list) -> None:
        self.clicks = clicks
        self.first = self

    async def scroll_into_view_if_needed(self, timeout=0):
        pass

    async def click(self, timeout=0):
        from ely.chrome import ChromeLost

        self.clicks.append("clic")
        raise ChromeLost("Chrome s'est déconnecté")

    async def evaluate(self, js):
        self.clicks.append("clic JavaScript")


class _LostPage:
    def __init__(self, clicks: list) -> None:
        self.clicks = clicks

    async def evaluate(self, js, *args):
        return True  # l'élément numéroté est bien dans la page

    def locator(self, selector):
        return _LostLocator(self.clicks)


class _LostBrowser:
    kind = "chrome"

    def __init__(self) -> None:
        self.clicks: list[str] = []

    @asynccontextmanager
    async def lock(self, key):
        yield

    async def page(self, key):
        return _LostPage(self.clicks)


async def test_chrome_lost_during_a_click_never_clicks_twice(user, monkeypatch):
    """Chrome se déconnecte pendant le clic sur « Commander » : le clic est peut-être parti. Ely ne reclique pas par
    JavaScript et prévient le modèle que la commande a peut-être eu lieu."""
    from ely.browser import manager

    ub = _LostBrowser()

    async def for_user(user_id):
        return ub

    monkeypatch.setattr(manager, "for_user", for_user)
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    r = await execute(ctx, "browser", {"action": "click", "ref": 12})
    assert ub.clicks == ["clic"]
    assert r.is_error and "Résultat incertain" in r.content and "déconnecté" in r.content


# ---------------------------------------------------------------------- modèles en panne ou trop courts
async def test_a_fallback_too_short_for_the_conversation_gets_a_condensed_history(fake, user):
    """Le modèle principal est en panne ; le secours (LM Studio, 8 000 tokens) refuse une longue conversation. Ely la
    condense à la taille du secours au lieu de viser celle du principal (trois essais vains, puis la tâche en erreur)."""
    from ely.llm.base import ModelInfo

    fake.models.append(ModelInfo(id="petit", provider="fake", context=8000))
    db.set_setting("model_fallbacks", "fake:petit")
    seen = []

    def script(model, system, messages, tools):
        text = json.dumps(messages, ensure_ascii=False)
        if model == "agent":
            return LLMError("clé refusée", kind="auth")
        if model == "fast":
            return '{"done": true, "missing": ""}' if "contrôleur qualité" in text else "Résumé : Franck prépare un voyage."
        seen.append(estimate_tokens(messages))
        if model == "petit" and estimate_tokens(messages) > 6000:
            return LLMError("contexte trop long", kind="context")
        return "Voici la suite du voyage."

    fake.script = script
    cid = new_conversation(user)
    for i in range(30):  # longue conversation : ~60 000 tokens
        save_message(cid, None, {"role": "user", "content": f"Étape {i} du voyage. " + "détails " * 900}, user["id"])
        save_message(cid, None, {"role": "assistant", "content": f"Noté pour l'étape {i}. " + "réponse " * 900}, user["id"])
    await runner.submit(user, cid, "Et maintenant ?")
    await wait_idle(cid, timeout=20)
    assert last_answer(cid)["content"] == "Voici la suite du voyage."
    assert db.val("SELECT status FROM runs WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,)) == "done"
    assert seen[-1] <= 6000 and len(seen) <= 3

    db.set_setting("model_fallbacks", "fake:petit,fake:grand")  # un autre secours tient la longueur : il prend le relais
    seen.clear()
    long = [{"role": "user", "content": "détails " * 30000}]
    resp = await registry.chat(system=["s"], messages=long)
    assert resp.text == "Voici la suite du voyage." and resp.model == "fake:grand" and seen == [estimate_tokens(long)] * 2


async def test_a_model_that_just_froze_waits_at_the_back_of_the_line(fake, monkeypatch):
    """Un modèle resté muet n'est pas réessayé en premier à l'étape suivante : chaque étape perdrait encore le délai."""
    calls = []

    async def script(model, system, messages, tools):
        calls.append(model)
        if model == "agent":
            await asyncio.sleep(5)  # figé
        return f"réponse de {model}"

    monkeypatch.setattr(registry_module, "CALL_DEADLINE", 0.3)
    monkeypatch.setattr(registry, "stalled", {})
    db.set_setting("model_fallbacks", "fake:fast")
    fake.script = script
    first = await registry.chat(system=["s"], messages=[{"role": "user", "content": "étape 1"}])
    started = time.monotonic()
    second = await registry.chat(system=["s"], messages=[{"role": "user", "content": "étape 2"}])
    assert first.model == second.model == "fake:fast" and time.monotonic() - started < 0.25
    assert calls == ["agent", "fast", "fast"]


class _LateLMStudio:
    """LM Studio lancé après Ely : un vrai serveur HTTP qui démarre quand on le décide."""

    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.server: uvicorn.Server | None = None

    def start(self) -> None:
        async def models(request):
            return JSONResponse({"data": [{"id": "qwen3.5-35b-a3b", "type": "llm", "state": "loaded",
                                           "loaded_context_length": 65536}]})

        async def chat(request):
            body = await request.json()
            text = json.dumps(body["messages"], ensure_ascii=False)
            answer = '{"done": true, "missing": ""}' if "contrôleur qualité" in text else "Bonjour Franck, je suis là."

            async def gen():
                yield f"data: {json.dumps({'choices': [{'delta': {'content': answer}}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")

        app = Starlette(routes=[Route("/api/v0/models", models), Route("/v1/models", models),
                                Route("/v1/chat/completions", chat, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.server.run, daemon=True).start()

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True


@pytest.fixture
def late_lmstudio(fake, monkeypatch):
    from ely.llm.openai_compat import OpenAICompatProvider

    lm = _LateLMStudio()
    registry.providers = {"lmstudio": OpenAICompatProvider("lmstudio", lm.url, "lm-studio", timeout=5)}
    for role in ("main", "fast", "local", "fallbacks"):
        db.set_setting(f"model_{role}", "auto")
    monkeypatch.setattr(registry_module, "RECHECK", 0.2)
    yield lm
    lm.stop()


async def test_ely_started_before_lm_studio_waits_for_it(late_lmstudio, user, monkeypatch):
    """Au démarrage du Mac, Ely est prête avant LM Studio : la première tâche patiente puis répond dès que LM Studio
    est lancé, au lieu d'échouer sur « aucun modèle » pendant 30 minutes."""
    from ely.agent import loop

    monkeypatch.setattr(loop, "RETRY_DELAYS", [0.4] * 5)
    await registry.refresh()
    assert registry.chain("main") == []  # LM Studio éteint : aucun modèle pour l'instant
    cid = new_conversation(user)
    await runner.submit(user, cid, "Bonjour Ely")
    await asyncio.sleep(0.3)
    late_lmstudio.start()
    await wait_idle(cid, timeout=20)
    assert last_answer(cid)["content"] == "Bonjour Franck, je suis là."
    assert last_answer(cid)["model"] == "lmstudio:qwen3.5-35b-a3b"


# ---------------------------------------------------------------------- historique
async def test_old_photos_are_not_resent_to_the_model_at_every_step(fake, user):
    """Une photo envoyée en début de conversation (Telegram, pièce jointe) n'est plus renvoyée au modèle plus loin :
    elle serait réencodée à chaque appel."""
    cid = new_conversation(user)
    photo = {"type": "image", "media_type": "image/jpeg", "data": "QUJD" * 1000}
    save_message(cid, None, {"role": "user", "content": [{"type": "text", "text": "Voici ma facture"}, photo]}, user["id"])
    for i in range(6):
        save_message(cid, None, {"role": "assistant", "content": f"Réponse {i}"}, user["id"])
        save_message(cid, None, {"role": "user", "content": f"Question {i}"}, user["id"])
    fake.script = lambda model, system, messages, tools: '{"done": true}' if model == "fast" else "D'accord."
    await runner.submit(user, cid, "Merci")
    await wait_idle(cid)
    sent = fake.calls[0]["messages"]
    assert "QUJD" not in json.dumps(sent)
    assert any(isinstance(m["content"], list) and {"type": "text", "text": "[image omise]"} in m["content"] for m in sent)


# ---------------------------------------------------------------------- routines
def _schedule(user, instruction: str, cron: str = "0 7 * * *") -> int:
    from ely.db import now

    return db.insert("schedules", user_id=user["id"], instruction=instruction, cron=cron, next_run=time.time() - 5,
                     enabled=1, created_at=now())


async def test_one_broken_routine_never_holds_back_the_others(fake, user):
    """Une routine illisible (horaire invalide, fuseau inconnu enregistré autrefois) ne retient plus celles des autres :
    elle est écartée avec la raison, les suivantes partent."""
    from ely import auth
    from ely.scheduler import run_due_schedules

    fake.script = lambda model, system, messages, tools: '{"done": true}' if model == "fast" else "C'est fait."
    lea = auth.create_user(f"lea{time.time_ns()}@x.fr", "Léa", "motdepasse")
    auth.update_user_settings(lea["id"], timezone="Europe/Pariss")  # faute de frappe d'une ancienne version
    broken = _schedule(user, "Rapport du matin", cron="61 * * * *")
    leas = _schedule(auth.get_user(lea["id"]), "Rappelle-moi d'arroser les plantes")
    await run_due_schedules()
    assert db.one("SELECT * FROM schedules WHERE id = ?", (broken,))["enabled"] == 0
    assert "erreur" in db.val("SELECT last_status FROM schedules WHERE id = ?", (broken,))
    cid = db.val("SELECT conversation_id FROM schedules WHERE id = ?", (leas,))
    await wait_idle(cid)
    assert last_answer(cid)["content"] == "C'est fait."  # son fuseau illisible ne fait pas échouer sa tâche
    assert db.val("SELECT last_status FROM schedules WHERE id = ?", (leas,)) == "terminée"


async def test_a_typo_in_the_timezone_is_refused(user):
    import httpx

    from ely import auth
    from ely.api.app import create_app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        h = {"Authorization": f"Bearer {auth.create_session(user['id'])}"}
        r = await c.patch("/api/me", json={"settings": {"timezone": "Europe/Pariss"}}, headers=h)
        assert r.status_code == 400 and "Fuseau horaire inconnu" in r.json()["detail"]
        r = await c.patch("/api/me", json={"settings": {"timezone": "America/Montreal"}}, headers=h)
        assert r.status_code == 200 and r.json()["settings"]["timezone"] == "America/Montreal"


async def test_a_failed_routine_tells_the_person(fake, user, monkeypatch):
    """La routine du matin échoue (aucun modèle n'a pu répondre) : la personne en est prévenue, et la liste des tâches
    planifiées garde son vrai résultat au lieu de « lancée »."""
    from ely import notify as notify_module
    from ely.scheduler import run_due_schedules

    sent = []

    async def notify(user_id, title, body, **kw):
        sent.append((user_id, title, body))

    monkeypatch.setattr(notify_module, "notify", notify)
    fake.script = lambda model, system, messages, tools: LLMError("clé refusée", kind="auth")
    sid = _schedule(user, "Résumé de mes mails")
    await run_due_schedules()
    await wait_idle(db.val("SELECT conversation_id FROM schedules WHERE id = ?", (sid,)))
    assert db.val("SELECT last_status FROM schedules WHERE id = ?", (sid,)) == "en échec"
    assert [(u, t) for u, t, _ in sent] == [(user["id"], "Ely · tâche planifiée en échec")] and "clé refusée" in sent[0][2]


# ---------------------------------------------------------------------- comptes et conversations
async def test_a_new_account_inherits_nothing_from_a_deleted_one(user):
    """Le compte supprimé avait le plus grand numéro : le suivant reprend ce numéro. Il ne voit ni ses fichiers, ni le
    profil du navigateur (cookies de sa banque, de sa messagerie), ni ses compétences."""
    import httpx

    from ely import auth
    from ely.api.app import create_app
    from ely.config import settings
    from ely.memory import store

    old = auth.create_user(f"ancien{time.time_ns()}@x.fr", "Ancien", "motdepasse")
    home = settings.user_dir(old["id"])
    (home / "files" / "prive.txt").write_text("relevé de compte")
    (home / "navigateur").mkdir()
    (home / "navigateur" / "Cookies").write_text("session banque")
    store.save_skill(old["id"], "Ma banque", "d", "Se connecter avec le code 1234")
    db.insert("usage", user_id=old["id"], model="fake:agent", input_tokens=10, output_tokens=5, purpose="agent", created_at=time.time())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        r = await c.delete(f"/api/admin/users/{old['id']}", headers={"Authorization": f"Bearer {auth.create_session(user['id'])}"})
    assert r.status_code == 200
    new = auth.create_user(f"nouveau{time.time_ns()}@x.fr", "Nouveau", "motdepasse")
    assert new["id"] == old["id"]
    home = settings.user_dir(new["id"])
    assert not (home / "files" / "prive.txt").exists() and not (home / "navigateur").exists()
    assert not db.all("SELECT * FROM skills WHERE user_id = ?", (new["id"],))
    assert not db.all("SELECT * FROM usage WHERE user_id = ?", (new["id"],))


async def test_deleting_a_conversation_while_ely_works_in_it(fake, user):
    """La tâche en cours est arrêtée proprement avant que la conversation disparaisse (sa note de fin ne tombe plus
    sur une conversation déjà supprimée)."""
    import httpx

    from ely import auth
    from ely.api.app import create_app

    started = asyncio.Event()

    async def script(model, system, messages, tools):
        started.set()
        await asyncio.sleep(30)
        return "trop tard"

    fake.script = script
    cid = new_conversation(user)
    await runner.submit(user, cid, "Cherche un vol pour Lisbonne")
    await started.wait()
    task = runner.states[cid].task
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        r = await c.delete(f"/api/conversations/{cid}", headers={"Authorization": f"Bearer {auth.create_session(user['id'])}"})
    assert r.status_code == 200
    await asyncio.sleep(0.2)
    assert task.done() and (task.cancelled() or task.exception() is None)
    assert not db.all("SELECT * FROM messages WHERE conversation_id = ?", (cid,))


# ---------------------------------------------------------------------- base de données
def test_task_lookups_use_an_index():
    """Le déroulé d'une tâche (vérification de l'objectif, journal, fin de tâche) se lit sans parcourir toute la base :
    sur une base de 1,2 Go, chaque lecture figeait le serveur 170 ms."""
    for sql in ("SELECT data FROM messages WHERE run_id = ? ORDER BY id", "SELECT * FROM tool_log WHERE run_id = ?"):
        plan = " ".join(str(tuple(r.values())) for r in db.all(f"EXPLAIN QUERY PLAN {sql}", (1,)))
        assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, (sql, plan)


async def test_old_screenshots_leave_the_database(fake, user):
    """Passé 30 jours, les captures d'écran quittent la base ; une photo jointe garde sa vignette (lien vers Fichiers)
    et la conversation reste utilisable avec le modèle."""
    from ely import scheduler

    cid = new_conversation(user)
    shot = save_message(cid, None, {"role": "tool", "tool_call_id": "c1", "name": "browser", "content": "Page",
                                    "images": [{"media_type": "image/jpeg", "data": "QUJD" * 5000}]}, user["id"])
    photo = save_message(cid, None, {"role": "user", "content": [
        {"type": "text", "text": "Ma facture"},
        {"type": "image", "media_type": "image/jpeg", "data": "WFla" * 5000, "src": "/files/Reçus/facture.jpg"}]}, user["id"])
    fresh = save_message(cid, None, {"role": "user", "content": [
        {"type": "image", "media_type": "image/png", "data": "Tk9V" * 100}]}, user["id"])
    old = time.time() - 31 * 86400
    db.run("UPDATE messages SET created_at = ? WHERE id IN (?, ?)", (old, shot, photo))
    db.set_setting("images_pruned_upto", shot - 1)
    assert await scheduler.prune_old_images() == 2
    data = {r["id"]: r["data"] for r in db.all("SELECT id, data FROM messages WHERE conversation_id = ?", (cid,))}
    assert "QUJD" not in data[shot] and "WFla" not in data[photo] and "/files/Reçus/facture.jpg" in data[photo]
    assert "Tk9V" in data[fresh]  # récente : intacte
    fake.script = lambda model, system, messages, tools: "Bien reçu."
    await runner.submit(user, cid, "Merci")
    await wait_idle(cid)
    sent = json.dumps([c for c in fake.calls if c["model"] == "agent"][-1]["messages"])
    assert "Tk9V" in sent and '"src"' not in sent  # le modèle ne reçoit jamais une image vide


# ---------------------------------------------------------------------- intégrations
@pytest.mark.skipif(not __import__("os").environ.get("ELY_BROWSER_EXECUTABLE"), reason="Chromium absent")
async def test_the_internal_browser_comes_back_after_a_crash(user):
    """Chromium interne fermé ou planté : relancé au prochain usage au lieu d'échouer sans fin."""
    from ely.browser import manager

    first = await manager.for_user(user["id"])
    await first.context.close()  # comme un plantage
    await asyncio.sleep(0.2)
    again = await manager.for_user(user["id"])
    try:
        assert again is not first
        page = await again.page("main")
        await page.goto("data:text/html,<title>ok</title>")
        assert await page.title() == "ok"
    finally:
        await manager.shutdown()  # Playwright est lié à la boucle de ce test
