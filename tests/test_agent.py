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


async def test_no_endless_loop_without_progress(fake, user):
    fake.script = script(lambda m, t: "Impossible.", verdicts=[{"done": False, "missing": "rien n'est fait"}] * 20)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Réserve-moi une table au restaurant ce soir")
    await wait_idle(cid)
    run = db.one("SELECT * FROM runs WHERE id = ?", (res["run_id"],))
    assert run["status"] == "done"
    assert json.loads(run["state"])["no_progress"] == 3


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
