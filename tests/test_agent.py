"""La boucle d'agent : action réelle, contrôle d'objectif, questions, messages en cours de route, reprise, repli."""
from __future__ import annotations

import asyncio
import json

from conftest import call, last_user_text, new_conversation, wait_idle

from ely.agent.runner import runner
from ely.db import db, now
from ely.llm.base import LLMError, LLMResponse


def script(agent, verdicts=None):
    """Aiguille les appels : contrôleur, titre, mémoire → réponses fixes ; le reste → agent()."""
    verdicts = list(verdicts or [])

    def fn(model, system, messages, tools):
        text = last_user_text(messages)
        if "contrôleur qualité" in text:
            v = verdicts.pop(0) if verdicts else {"done": True, "missing": ""}
            return json.dumps(v)
        if "Donne un titre" in text:
            return "Titre de test"
        if "mémoire d'Ely" in text:
            return '{"profile": "## Identité\\n- Prénom : Franck", "facts": ["Franck aime le café serré"], "skill": null}'
        if "Résume cet échange" in text:
            return "Résumé."
        return agent(messages, tools)

    return fn


def tool_results(messages):
    return [m for m in messages if m["role"] == "tool"]


async def test_action_is_really_performed(fake, user):
    def agent(messages, tools):
        assert "contacts_save" in [t["name"] for t in tools]
        if not tool_results(messages):
            return call("contacts_save", name="Jean Dupont", phone="0601020304")
        return "C'est fait : Jean Dupont est dans tes contacts."

    fake.script = script(agent)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Ajoute Jean Dupont 0601020304 à mes contacts")
    assert res["mode"] == "started"
    await wait_idle(cid)
    assert db.one("SELECT * FROM contacts WHERE user_id = ? AND name = 'Jean Dupont'", (user["id"],))
    run = db.one("SELECT * FROM runs WHERE id = ?", (res["run_id"],))
    assert run["status"] == "done" and run["steps"] == 1
    roles = [json.loads(m["data"])["role"] for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    assert roles == ["user", "assistant", "tool", "assistant"]


async def test_controller_forces_agent_to_continue(fake, user):
    """Le modèle dit « je vais le faire » sans agir : le contrôleur le relance jusqu'à l'action réelle."""
    def agent(messages, tools):
        controls = [m for m in messages if m["role"] == "user" and "Contrôle automatique" in str(m["content"])]
        if not controls:
            return "Je vais ajouter ce rendez-vous à ton agenda."
        if not tool_results(messages):
            return call("calendar_add", title="Dentiste", start="2030-03-04T10:00")
        return "Rendez-vous ajouté le 4 mars à 10 h."

    fake.script = script(agent, verdicts=[{"done": False, "missing": "le rendez-vous n'a pas été ajouté"}, {"done": True}])
    cid = new_conversation(user)
    await runner.submit(user, cid, "Ajoute un rendez-vous chez le dentiste le 4 mars 2030 à 10h")
    await wait_idle(cid)
    assert db.one("SELECT * FROM events WHERE user_id = ? AND title = 'Dentiste'", (user["id"],))
    datas = [json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    assert any(d.get("kind") == "control" for d in datas)
    assert datas[-1]["content"].startswith("Rendez-vous ajouté")


async def test_verifier_knows_today_date(fake, user):
    """Le contrôleur juge « nous serons le … » d'après la date du jour, comme l'agent, pas d'après ses connaissances."""
    from ely.agent.prompts import today_text

    prompts = []

    def fn(model, system, messages, tools):
        text = last_user_text(messages)
        if "contrôleur qualité" in text:
            prompts.append(text)
        return script(lambda m, t: "Dans 25 jours, nous serons le 27 octobre.")(model, system, messages, tools)

    fake.script = fn
    cid = new_conversation(user)
    await runner.submit(user, cid, "Quelle date serons-nous dans 25 jours ?")
    await wait_idle(cid)
    assert prompts and today_text(user).split(", il est")[0] in prompts[0]


async def test_user_only_blocker_is_accepted_without_relaunch(fake, user):
    """Compte Google à reconnecter : le contrôleur sait qu'un tel blocage se signale, il ne relance pas l'agent."""
    def fn(model, system, messages, tools):
        text = last_user_text(messages)
        if "contrôleur qualité" in text:
            accepted = "seul l'utilisateur peut lever" in text and "invalid_grant" in text
            return json.dumps({"done": accepted, "missing": "" if accepted else "reconnecter Google"})
        return script(lambda m, t: "Accès Google expiré (invalid_grant) : reconnectez le compte dans Réglages → Connexions → Google.")(
            model, system, messages, tools)

    fake.script = fn
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Nettoie ma boîte Gmail")
    await wait_idle(cid)
    run = db.one("SELECT * FROM runs WHERE id = ?", (res["run_id"],))
    datas = [json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    assert run["status"] == "done" and not any(d.get("kind") == "control" for d in datas)


async def test_no_endless_loop_without_progress(fake, user):
    """Relances sans progrès : la réponse est rendue, mais la tâche n'est pas présentée comme réussie (statut, réserve
    dite à la personne) et n'apprend aucune procédure."""
    fake.script = script(lambda m, t: "Impossible.", verdicts=[{"done": False, "missing": "aucune table n'est réservée"}] * 20)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Réserve-moi une table au restaurant ce soir")
    await wait_idle(cid)
    run = db.one("SELECT * FROM runs WHERE id = ?", (res["run_id"],))
    assert run["status"] == "unverified"
    assert json.loads(run["state"])["no_progress"] == 3
    last = json.loads(db.val("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,)))
    assert last["kind"] == "note" and "pas pu confirmer" in last["content"] and "aucune table n'est réservée" in last["content"]


async def test_a_verdict_written_as_text_is_still_a_refusal(fake, user):
    """« "done": "false" » écrit en texte par le contrôleur est un refus, pas une validation ; un contrôleur muet laisse
    la réponse arriver, sans confirmer la tâche."""
    def agent(messages, tools):
        if not tool_results(messages) and not any("Contrôle automatique" in str(m["content"]) for m in messages if m["role"] == "user"):
            return "Je vais l'ajouter."
        if not tool_results(messages):
            return call("contacts_save", name="Lucie Martin", phone="0611223344")
        return "Lucie Martin est dans vos contacts."

    fake.script = script(agent, verdicts=[{"done": "false", "missing": "le contact n'est pas enregistré"}, {"done": "true"}])
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Ajoute Lucie Martin 0611223344 à mes contacts")
    await wait_idle(cid)
    assert db.one("SELECT * FROM contacts WHERE user_id = ? AND name = 'Lucie Martin'", (user["id"],))
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "done"

    fake.script = script(lambda m, t: "Voici le résumé demandé.", verdicts=[{"verdict": "ok"}] * 4)
    res = await runner.submit(user, cid, "Résume-moi les nouvelles de la semaine")
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "unverified"


async def test_ask_user_waits_for_answer(fake, user):
    def agent(messages, tools):
        results = tool_results(messages)
        if not results:
            return call("ask_user", question="Quel code as-tu reçu par SMS ?")
        return f"Code utilisé : {results[-1]['content']}"

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Connecte-toi à mon compte")
    for _ in range(100):
        await asyncio.sleep(0.02)
        if runner.states[cid].status == "waiting_user":
            break
    st = runner.states[cid]
    assert st.status == "waiting_user" and "SMS" in st.ask["question"]
    res = await runner.submit(user, cid, "482913")
    assert res["mode"] == "answer"
    await wait_idle(cid)
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert "482913" in last["content"]


class FakeSMTP:
    """Serveur d'envoi simulé : chaque transmission est notée ; la première coupe la connexion en cours de route."""
    sent: list[str] = []
    failures: list[Exception] = []

    def __init__(self, host, port, context=None, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg["To"])
        if FakeSMTP.failures:
            raise FakeSMTP.failures.pop(0)


def _smtp_mailbox(user, monkeypatch, *failures):
    import smtplib

    from ely.integrations import put

    put(user["id"], "email", {"address": "franck@exemple.fr", "password": "x", "smtp_host": "smtp.gmail.com",
                              "smtp_port": 465, "imap_host": "imap.gmail.com"})
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.sent, FakeSMTP.failures = [], list(failures)


async def test_a_cut_while_sending_is_uncertain_but_a_refusal_is_not(fake, user, monkeypatch):
    """Connexion coupée pendant la transmission : l'e-mail est peut-être parti, le résultat le dit. Destinataire
    refusé par le serveur : rien n'est parti, c'est un échec ordinaire."""
    import smtplib

    from conftest import new_conversation as _nc

    from ely.tools import ToolContext, execute

    async def _noop(*a, **k):
        pass

    _smtp_mailbox(user, monkeypatch, smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
                  smtplib.SMTPRecipientsRefused({"paul@exemple.fr": (550, b"no such user")}))
    ctx = ToolContext(user=user, conversation_id=_nc(user), run_id=0, emit=_noop)
    args = {"to": "paul@exemple.fr", "subject": "Facture", "body": "Ci-joint la facture."}
    r = await execute(ctx, "email_send", args)
    assert r.is_error and r.content.startswith("Résultat incertain"), r.content
    r = await execute(ctx, "email_send", args)
    assert r.is_error and not r.content.startswith("Résultat incertain") and "SMTPRecipientsRefused" in r.content


async def test_an_uncertain_email_is_never_sent_again_without_asking(fake, user, monkeypatch):
    """La connexion lâche pendant l'envoi : l'e-mail est peut-être parti. Ely ne le renvoie pas à l'identique de son
    propre chef ; elle demande, et ne le renvoie qu'avec l'accord de la personne."""
    import smtplib

    _smtp_mailbox(user, monkeypatch, smtplib.SMTPServerDisconnected("Connection unexpectedly closed"))
    mail = {"to": "paul@exemple.fr", "subject": "Facture", "body": "Ci-joint la facture."}

    def agent(messages, tools):
        results = tool_results(messages)
        last = results[-1] if results else None
        if not results or (last["name"] == "ask_user" and "oui" in last["content"]):
            return call("email_send", **mail)
        if last["content"].startswith("Résultat incertain"):
            return call("email_send", **mail)  # le modèle tente de renvoyer aussitôt
        if last["content"].startswith("Action non relancée"):
            return call("ask_user", question="L'envoi a été coupé : je le renvoie ?")
        return "E-mail envoyé à Paul."

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Envoie la facture à paul@exemple.fr")
    for _ in range(200):
        await asyncio.sleep(0.02)
        if runner.states[cid].status == "waiting_user":
            break
    assert runner.states[cid].status == "waiting_user"
    assert FakeSMTP.sent == ["paul@exemple.fr"]  # la relance immédiate n'a pas atteint le serveur
    await runner.submit(user, cid, "oui, renvoie-le")
    await wait_idle(cid)
    assert FakeSMTP.sent == ["paul@exemple.fr", "paul@exemple.fr"]


async def test_message_added_during_task_is_taken_into_account(fake, user):
    seen = []

    async def agent(messages, tools):
        texts = " | ".join(str(m.get("content")) for m in messages if m["role"] == "user")
        seen.append(texts)
        if len(seen) == 1:
            await asyncio.sleep(0.3)
            return call("weather_fake_missing_tool")
        return "Bien noté, avec les deux demandes."

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Prépare la liste de courses")
    await asyncio.sleep(0.1)
    res = await runner.submit(user, cid, "et ajoute du pain")
    assert res["mode"] == "queued"
    await wait_idle(cid)
    assert "ajoute du pain" in seen[-1]
    assert db.val("SELECT COUNT(*) FROM runs WHERE conversation_id = ?", (cid,)) == 1


async def test_resume_after_restart_never_replays_uncertain_action(fake, user):
    cid = new_conversation(user)
    uid = user["id"]
    m1 = db.insert("messages", conversation_id=cid, role="user", data=json.dumps({"role": "user", "content": "Envoie le mail à Paul"}),
                   created_at=now())
    run_id = db.insert("runs", conversation_id=cid, user_id=uid, status="running", objective="Envoie le mail à Paul",
                       state=json.dumps({"start_message": m1}), created_at=now(), updated_at=now())
    db.run("UPDATE messages SET run_id = ? WHERE id = ?", (run_id, m1))
    assistant = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_x", "name": "email_send", "arguments": {"to": "paul@x.fr", "body": "Salut"}}]}
    db.insert("messages", conversation_id=cid, run_id=run_id, role="assistant", data=json.dumps(assistant), created_at=now())

    def agent(messages, tools):
        lost = [m for m in tool_results(messages) if "redémarré" in m["content"]]
        assert lost, "le résultat perdu doit être signalé, pas l'action rejouée"
        return "J'ai vérifié : le mail est parti."

    fake.script = script(agent)
    n = await runner.resume_all()
    assert n >= 1
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (run_id,)) == "done"


async def test_fallback_to_next_model(fake, user):
    from ely.llm import registry

    db.set_setting("model_fallbacks", "fake:backup")

    def agent(messages, tools):
        return "Réponse du modèle de secours."

    def fn(model, system, messages, tools):
        if model == "agent":
            return LLMError("clé refusée", kind="auth", status=401)
        return script(agent)(model, system, messages, tools)

    fake.script = fn
    cid = new_conversation(user)
    await runner.submit(user, cid, "Quelle est la capitale de l'Australie ?")
    await wait_idle(cid)
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert last["model"] == "fake:backup"
    db.set_setting("model_fallbacks", "auto")
    assert registry.chain("main")[0] == "fake:agent"


async def test_learning_updates_profile_and_memories(fake, user):
    from ely.memory import store

    fake.script = script(lambda m, t: call("remember", fact="Franck a un chat nommé Pixel") if not tool_results(m) else "Noté !")
    cid = new_conversation(user)
    await runner.submit(user, cid, "Retiens que mon chat s'appelle Pixel")
    await wait_idle(cid)
    await asyncio.sleep(0.3)  # apprentissage en arrière-plan
    assert "Franck" in store.get_profile(user["id"])
    found = await store.search_memories(user["id"], "chat Pixel")
    assert any("Pixel" in m["content"] for m in found)
    assert db.val("SELECT title FROM conversations WHERE id = ?", (cid,)) == "Titre de test"


async def test_parallel_tool_calls(fake, user):
    def agent(messages, tools):
        if not tool_results(messages):
            return LLMResponse(text="", tool_calls=[
                call("contacts_save", name="A").tool_calls[0], call("contacts_save", name="B").tool_calls[0]])
        return "Deux contacts ajoutés."

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Ajoute les contacts A et B")
    await wait_idle(cid)
    names = {r["name"] for r in db.all("SELECT name FROM contacts WHERE user_id = ?", (user["id"],))}
    assert {"A", "B"} <= names


async def test_cancel(fake, user):
    async def agent(messages, tools):
        await asyncio.sleep(5)
        return "trop tard"

    fake.script = script(agent)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Tâche très longue")
    await asyncio.sleep(0.1)
    assert await runner.cancel(cid)
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "cancelled"


async def test_delegate_runs_subagents_in_parallel(fake, user):
    """delegate lance des sous-agents en parallèle ; chacun agit avec les outils et rend un rapport."""
    started = []

    async def agent(messages, tools):
        first = last_user_text(messages[:1])
        if "Ta sous-tâche" in first:
            started.append(first)
            await asyncio.sleep(0.2)  # si les sous-agents étaient séquentiels, le test serait lent
            if not tool_results(messages):
                name = "Alpha" if "Alpha" in first else "Beta"
                return call("contacts_save", name=name)
            return f"Rapport : contact ajouté ({'Alpha' if 'Alpha' in first else 'Beta'})."
        if not tool_results(messages):
            return call("delegate", tasks=["Ajoute le contact Alpha", "Ajoute le contact Beta"])
        return "Les deux sous-agents ont terminé : " + tool_results(messages)[-1]["content"][:200]

    fake.script = script(agent)
    cid = new_conversation(user)
    t0 = asyncio.get_running_loop().time()
    await runner.submit(user, cid, "Ajoute Alpha et Beta à mes contacts en parallèle")
    await wait_idle(cid)
    names = {r["name"] for r in db.all("SELECT name FROM contacts WHERE user_id = ?", (user["id"],))}
    assert {"Alpha", "Beta"} <= names and len(started) == 4
    assert asyncio.get_running_loop().time() - t0 < 2.0
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert "Rapport" in last["content"]


def _three_providers(fake):
    """LM Studio choisi pour l'agent, abonnement ChatGPT pour l'escalade, et une clé Anthropic jamais choisie."""
    from conftest import FakeProvider
    from ely.llm import registry
    from ely.llm.base import ModelInfo

    provs = {}
    for name, mid in (("lmstudio", "google/gemma-4-26b-a4b"), ("chatgpt", "gpt-6-astra"), ("anthropic", "claude-opus-5")):
        p = FakeProvider(name)
        p.models = [ModelInfo(id=mid, provider=name, context=32768 if name == "lmstudio" else 200_000)]
        registry.providers[name] = p
        registry.catalog[name] = p.models
        registry.status[name] = "ok (1 modèle)"
        provs[name] = p
    provs["anthropic"].script = lambda **k: "réponse payante"
    provs["chatgpt"].script = lambda **k: "réponse de l'abonnement"
    db.set_setting("model_main", "lmstudio:google/gemma-4-26b-a4b")
    db.set_setting("model_strong", "chatgpt:gpt-6-astra")
    return provs


async def test_auto_fallbacks_never_use_an_unchosen_paid_model(fake, user):
    from ely.llm import registry

    provs = _three_providers(fake)
    provs["lmstudio"].script = lambda **k: LLMError("LM Studio a planté", kind="error")
    assert "anthropic:claude-opus-5" not in registry.chain("main")
    r = await registry.chat(role="main", system=[], messages=[{"role": "user", "content": "Bonjour"}])
    assert r.model == "chatgpt:gpt-6-astra" and not provs["anthropic"].calls
    db.set_setting("model_fallbacks", "anthropic:claude-opus-5")  # choisi explicitement : là, oui
    assert registry.chain("main")[1] == "anthropic:claude-opus-5"
    db.set_setting("model_fallbacks", "auto")


async def test_switching_model_says_why(fake, user):
    """« Astra indisponible » ne suffit pas pour agir : la raison du basculement est dite dans la conversation."""
    from ely.llm import registry

    provs = _three_providers(fake)
    provs["lmstudio"].script = lambda **k: LLMError("chatgpt : connexion à l'abonnement expirée (401)", kind="auth")
    notices = []

    async def on_switch(ref, reason):
        notices.append(reason)

    await registry.chat(role="main", system=[], messages=[{"role": "user", "content": "Bonjour"}], on_switch=on_switch)
    assert notices == ["lmstudio:google/gemma-4-26b-a4b indisponible (chatgpt : connexion à l'abonnement expirée (401)), "
                       "bascule sur chatgpt:gpt-6-astra"], notices


async def test_context_overflow_condenses_instead_of_switching_model(fake, user):
    provs = _three_providers(fake)
    state = {"overflowed": False}

    def local(model, system, messages, tools):
        if not last_user_text(messages).startswith("Tu es le contrôleur") and not state["overflowed"]:
            state["overflowed"] = True
            return LLMError("request exceeds the model's context length (32768)", kind="context", status=400)
        return script(lambda m, t: "Voici ta réponse, avec gemma.")(model, system, messages, tools)

    provs["lmstudio"].script = local
    cid = new_conversation(user)
    await runner.submit(user, cid, "Résume-moi les nouvelles du jour.")
    await wait_idle(cid)
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert state["overflowed"] and last["model"] == "lmstudio:google/gemma-4-26b-a4b"
    assert not provs["anthropic"].calls and not provs["chatgpt"].calls


async def test_vouvoiement_by_default_and_tutoiement_on_request(fake, user):
    from ely import auth

    systems = []

    def fn(model, system, messages, tools):
        text = last_user_text(messages)
        if "mémoire d'Ely" in text:  # apprentissage après l'échange
            return json.dumps({"profile": None, "facts": [], "address": "tu" if "tutoie-moi" in text else None, "skill": None})
        if "contrôleur qualité" in text:
            return json.dumps({"done": True, "missing": ""})
        if "Donne un titre" in text:
            return "Titre de test"
        systems.append("\n".join(system))
        return "D'accord, je te tutoie." if "tutoie" in text else "Bonjour."

    fake.script = fn
    cid = new_conversation(user)
    await runner.submit(user, cid, "Tu peux me tutoyer : tutoie-moi, s'il te plaît.")
    await wait_idle(cid)
    assert "s'adresser à elle : vouvoiement" in systems[0]  # par défaut, Ely vouvoie
    await asyncio.sleep(0.3)  # apprentissage en arrière-plan
    fresh = auth.get_user(user["id"])
    assert fresh["settings"].get("address") == "tu"
    cid2 = new_conversation(user)
    await runner.submit(fresh, cid2, "Quel temps fera-t-il demain ?")
    await wait_idle(cid2)
    assert "s'adresser à elle : tutoiement" in systems[-1]
    cid3 = new_conversation(user)
    await runner.submit(fresh, cid3, "Finalement, ne me tutoie pas.")
    await wait_idle(cid3)
    await asyncio.sleep(0.3)
    assert auth.get_user(user["id"])["settings"].get("address") == "vous"


# ---------------------------------------------------------------- choix du modèle : escalade, demande explicite, auto-amélioration
def with_strong_model(fake):
    """Le faux fournisseur propose aussi un modèle « fort », choisi pour l'escalade (Astra chez Franck)."""
    from ely.llm import registry
    from ely.llm.base import ModelInfo

    fake.models = [*fake.models, ModelInfo(id="fort", provider="fake", context=200_000)]
    registry.catalog["fake"] = fake.models
    db.set_setting("model_strong", "fake:fort")


def routed(agent, verdicts=None):
    """Comme script(), en notant le modèle de chaque appel de l'agent lui-même."""
    used, cur = [], {}

    def mark(messages, tools):
        used.append(cur["model"])
        return agent(messages, tools)

    inner = script(mark, verdicts)

    def fn(model, system, messages, tools):
        cur["model"] = model
        return inner(model, system, messages, tools)

    return fn, used


def model_notices(q) -> list[str]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev["type"] == "model" and ev.get("reason"):
            out.append(ev["reason"])
    return out


async def test_escalation_as_soon_as_the_goal_is_not_met(fake, user):
    """Dès le premier rejet du contrôleur, la suite de la tâche passe au modèle d'escalade, et Ely le dit."""
    with_strong_model(fake)

    def agent(messages, tools):
        if not tool_results(messages) and not any("Contrôle automatique" in str(m["content"]) for m in messages if m["role"] == "user"):
            return "Je vais ajouter ce rendez-vous."
        if not tool_results(messages):
            return call("calendar_add", title="Ostéopathe", start="2030-05-06T09:00")
        return "Rendez-vous ajouté le 6 mai à 9 h."

    fake.script, used = routed(agent, verdicts=[{"done": False, "missing": "le rendez-vous n'est pas dans l'agenda"}, {"done": True}])
    q = runner.subscribe(user["id"])
    try:
        cid = new_conversation(user)
        await runner.submit(user, cid, "Ajoute l'ostéopathe le 6 mai 2030 à 9 h")
        await wait_idle(cid)
    finally:
        runner.unsubscribe(user["id"], q)
    assert used[0] == "agent" and set(used[1:]) == {"fort"}, used
    assert any("objectif pas encore atteint" in r and "fake:fort" in r for r in model_notices(q))


async def test_identical_read_only_call_is_refused_and_escalates(fake, user):
    """Un petit modèle refaisait la même recherche toutes les 9 s : au 3e appel identique en lecture seule, Ely
    ne l'exécute plus, le dit au modèle et passe au modèle d'escalade ; une autre requête reste exécutée."""
    with_strong_model(fake)

    def agent(messages, tools):
        done = tool_results(messages)
        if len(done) < 4:
            return call("calendar_list", start="2030-05-06", end="2030-05-07")
        if len(done) == 4:
            return call("calendar_list", start="2030-05-08", end="2030-05-09")
        return "Aucun rendez-vous."

    fake.script, used = routed(agent)
    q = runner.subscribe(user["id"])
    try:
        cid = new_conversation(user)
        await runner.submit(user, cid, "Qu'ai-je à l'agenda le 6 mai 2030 ?")
        await wait_idle(cid)
    finally:
        runner.unsubscribe(user["id"], q)
    results = [d for d in (json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,)))
               if d["role"] == "tool"]
    assert [r["is_error"] for r in results] == [False, False, True, True, False]
    assert "Appel identique déjà fait 2 fois" in results[2]["content"]
    assert used[:3] == ["agent"] * 3 and set(used[3:]) == {"fort"}, used
    assert any("répète la même action" in r for r in model_notices(q))


async def test_scheduled_task_without_any_action_is_not_accepted_when_controller_fails(fake, user):
    """Un petit modèle rédigeait le compte rendu d'une routine sans avoir appelé un seul outil, et le contrôleur muet
    l'acceptait : sans verdict, une tâche planifiée sans aucune action est relancée (et passe au modèle d'escalade)."""
    with_strong_model(fake)

    def agent(messages, tools):
        if not tool_results(messages) and not any("Aucun outil n'a été utilisé" in str(m["content"]) for m in messages if m["role"] == "user"):
            return "J'ai vérifié Gmail : aucun nouveau mail Digiposte."
        if not tool_results(messages):
            return call("calendar_list", start="2030-05-06", end="2030-05-07")
        return "Agenda consulté : aucun événement."

    routed_fn, used = routed(agent)

    def fn(model, system, messages, tools):
        if "contrôleur qualité" in last_user_text(messages):
            return "illisible"
        return routed_fn(model, system, messages, tools)

    fake.script = fn
    cid = new_conversation(user)
    await runner.submit(user, cid, "[Tâche planifiée #4] Surveille mes mails Digiposte", kind="scheduled")
    await wait_idle(cid)
    datas = [json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    assert any(d.get("kind") == "control" and "Aucun outil" in d["content"] for d in datas)
    assert datas[-1]["content"] == "Agenda consulté : aucun événement."
    assert used[0] == "agent" and set(used[1:]) == {"fort"}, used


async def test_scheduled_task_that_must_notify_is_relaunched_until_notify_is_called(fake, user, monkeypatch):
    """Veille de prix « notifier Franck via Telegram » : le petit modèle avait rendu un rapport aux liens fictifs
    (« [Lien de la fiche produit Cdiscount] ») sans jamais appeler notify, et le contrôleur, tout aussi petit, l'avait
    accepté. Ces deux manques sont constatés sans modèle : la tâche est relancée (sur le modèle fort) jusqu'à l'envoi."""
    from ely.tools import planning

    with_strong_model(fake)
    sent = []

    async def fake_notify(user_id, title, body, url=None, receipt=None, **kw):
        receipt.update(push_accepted=1, push_total=1, telegram_status="accepted", telegram_ids=[7], telegram_total=1)
        sent.append(body)
        return 1

    monkeypatch.setattr(planning, "send_notification", fake_notify)

    def agent(messages, tools):
        controls = [str(m["content"]) for m in messages if m["role"] == "user" and "Contrôle automatique" in str(m["content"])]
        if not tool_results(messages):
            return call("calendar_list", start="2030-05-06", end="2030-05-07")
        if not controls:
            return "Meilleure offre : M310 à 90 € chez Tradevisio. Lien : [Lien de la fiche produit Cdiscount]"
        if "fictives" in controls[-1]:
            return "Meilleure offre : M310 à 90 € chez Tradevisio, https://www.cdiscount.com/m310"
        if "notify" in controls[-1] and not any(m.get("name") == "notify" for m in tool_results(messages)):
            return call("notify", title="Veille Lefant", message="M310 à 90 € chez Tradevisio : https://www.cdiscount.com/m310")
        return "Offre envoyée sur Telegram."

    fake.script, used = routed(agent)  # le contrôleur simulé accepte tout : seuls les garde-fous relancent
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "[Tâche planifiée #9] Chercher le prix du Lefant M310 et notifier Franck via Telegram", kind="scheduled")
    await wait_idle(cid)
    datas = [json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    controls = [d["content"] for d in datas if d.get("kind") == "control"]
    assert len(controls) == 2 and "fictives" in controls[0] and "notify" in controls[1], controls
    assert sent == ["M310 à 90 € chez Tradevisio : https://www.cdiscount.com/m310"]
    assert datas[-1]["content"] == "Offre envoyée sur Telegram."
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "done"
    assert used[:2] == ["agent"] * 2 and set(used[2:]) == {"fort"}, used


async def test_conditional_notification_is_not_demanded_twice(fake, user):
    """Routine « notifier seulement s'il y a du nouveau » sans nouveauté : la remarque n'est faite qu'une fois, puis le
    verdict du contrôleur s'applique ; la tâche se termine sans notification."""
    def agent(messages, tools):
        if not tool_results(messages):
            return call("calendar_list", start="2030-05-06", end="2030-05-07")
        return "Rien de nouveau : pas de notification à envoyer."

    fake.script = script(agent)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "[Tâche planifiée #5] S'il y a un nouvel événement, préviens-moi via notify", kind="scheduled")
    await wait_idle(cid)
    datas = [json.loads(m["data"]) for m in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]
    assert sum(1 for d in datas if d.get("kind") == "control") == 1
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "done"


async def test_strong_model_on_request(fake, user):
    """« Prenez le modèle fort » : Ely y passe dès le premier appel. Une simple question sur le modèle fort ne
    change rien, et « utilise Opus » passe sur le modèle fort tant que Claude n'est pas relié."""
    with_strong_model(fake)
    q = runner.subscribe(user["id"])
    try:
        for text, expected, notice in (
            ("Prenez le modèle fort pour me résumer la loi de finances", "fort", "modèle fort demandé"),
            ("Le modèle fort répond-il vite ?", "agent", None),
            ("Utilise Opus pour comparer ces deux devis", "fort", "Claude Opus n'est pas encore relié"),
            ("use the strong model to plan my week", "fort", "modèle fort demandé"),
        ):
            fake.script, used = routed(lambda messages, tools: "Voici.")
            cid = new_conversation(user)
            await runner.submit(user, cid, text)
            await wait_idle(cid)
            notices = model_notices(q)
            assert used == [expected], (text, used)
            assert (notice is None and not notices) or any(notice in n for n in notices), (text, notices)
    finally:
        runner.unsubscribe(user["id"], q)


async def test_self_improvement_uses_its_own_model(fake, user):
    """Réglages → Modèles → Auto-amélioration : automatique = le modèle d'escalade ; un choix explicite s'applique
    à la session suivante, sans redémarrage. Les conversations ordinaires restent sur le modèle principal."""
    from ely.llm import registry
    from ely.selfdev.tools import start_session

    with_strong_model(fake)
    assert registry.roles_view()["selfdev"] == {"configured": "auto", "effective": "fake:fort", "effort": "high"}
    fake.script, used = routed(lambda messages, tools: "Rien à améliorer aujourd'hui.")
    cid = start_session(user, "Sois plus rapide sur Doctolib")
    await wait_idle(cid)
    assert used and set(used) == {"fort"}, used

    db.set_setting("model_selfdev", "fake:agent")  # choisi dans les réglages
    fake.script, used = routed(lambda messages, tools: "Rien à améliorer aujourd'hui.")
    start_session(user, "Sois plus fiable sur LinkedIn")
    await wait_idle(cid)
    assert used and set(used) == {"agent"}, used
    db.set_setting("model_selfdev", "auto")


def test_model_requests_are_recognised_only_as_requests():
    from ely.agent.loop import asked_model

    assert asked_model("Passe sur le modèle le plus puissant, s'il te plaît") == "strong"
    assert asked_model("Utilisez Claude Fable pour relire ce contrat") == "fable"
    assert asked_model("switch to the strongest model") == "strong"
    assert asked_model("Je n'utilise pas le modèle fort pour ça") is None
    assert asked_model("Quel est le modèle fort configuré ?") is None
    assert asked_model("Relis l'opus 3 de Chopin") is None


async def test_two_tasks_ending_together_both_enrich_the_profile(fake, user):
    """Deux tâches finissent en même temps : chacune apprend quelque chose sur Franck, et le profil garde les deux
    (la seconde mise à jour part de la première au lieu de l'écraser)."""
    from ely.agent.runner import save_message
    from ely.memory import store
    from ely.memory.learner import learn_from_run

    async def learner(model, system, messages, tools):
        text = last_user_text(messages)
        current = text.split("PROFIL ACTUEL :\n", 1)[1].split("\n\n", 1)[0]
        lines = [l for l in current.splitlines() if l.startswith("- ")]
        await asyncio.sleep(0.2)  # le modèle local prend son temps
        new = "- Ville : Poitiers" if "J'habite à Poitiers" in text else "- Prénom : Franck"
        return json.dumps({"profile": "## Identité\n" + "\n".join(lines + [new]), "facts": [], "skill": None})

    fake.script = learner
    runs = []
    for said in ("Je m'appelle Franck, retiens mon prénom.", "J'habite à Poitiers, retiens ma ville."):
        cid = new_conversation(user)
        run = db.insert("runs", conversation_id=cid, user_id=user["id"], status="done", objective=said, created_at=now(),
                        updated_at=now())
        for m in ({"role": "user", "content": said}, {"role": "assistant", "content": "C'est noté, je m'en souviendrai " * 6}):
            save_message(cid, run, m)
        runs.append((cid, run))
    await asyncio.gather(*(learn_from_run(user, cid, run) for cid, run in runs))
    profile = store.get_profile(user["id"])
    assert "Prénom : Franck" in profile and "Ville : Poitiers" in profile, profile
