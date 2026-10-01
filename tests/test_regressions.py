"""Non-régression des bogues trouvés en relecture."""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from conftest import call, last_user_text, new_conversation, wait_idle
from test_agent import script, tool_results

from ely.agent.runner import runner, save_message
from ely.db import db, now
from ely.llm.base import LLMError, LLMResponse
from ely.tools import TOOLS, ToolContext, execute


async def _noop(*a, **k):
    pass


async def wait_status(cid: int, status: str, timeout: float = 5) -> None:
    for _ in range(int(timeout / 0.02)):
        st = runner.states.get(cid)
        if st and st.status == status:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(status)


async def test_question_survives_graceful_restart(fake, user):
    def agent(messages, tools):
        if not tool_results(messages):
            return call("ask_user", question="Quel est ton code client ?")
        return f"Merci, {tool_results(messages)[-1]['content']}"

    fake.script = script(agent)
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Connecte-toi à mon espace client")
    await wait_status(cid, "waiting_user")
    await runner.shutdown()  # redémarrage propre (SIGTERM, auto-déploiement…)
    runner.shutting_down = False
    run = db.one("SELECT status, state FROM runs WHERE id = ?", (res["run_id"],))
    assert run["status"] == "waiting_user" and json.loads(run["state"])["pending_ask"]["question"].startswith("Quel est")
    runner.states.pop(cid, None)
    await runner.resume_all()
    await wait_status(cid, "waiting_user")
    assert runner.states[cid].ask["question"] == "Quel est ton code client ?"
    await runner.submit(user, cid, "C-42")
    await wait_idle(cid)
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert "C-42" in last["content"]


async def test_scheduled_message_is_not_taken_as_an_answer(fake, user):
    def agent(messages, tools):
        if not tool_results(messages):
            return call("ask_user", question="Quelle couleur ?")
        return "ok " + tool_results(messages)[-1]["content"]

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Choisis une couleur avec moi")
    await wait_status(cid, "waiting_user")
    res = await runner.submit(user, cid, "[Tâche planifiée #9] Rappel", kind="scheduled")
    assert res["mode"] == "queued" and runner.states[cid].status == "waiting_user"
    await runner.submit(user, cid, "Bleu")
    await wait_idle(cid)
    answers = [m for m in db.all("SELECT data FROM messages WHERE conversation_id = ? AND role = 'tool'", (cid,))]
    assert "Bleu" in json.loads(answers[0]["data"])["content"]


async def test_two_questions_in_one_turn_are_asked_one_after_the_other(fake, user):
    def agent(messages, tools):
        if not tool_results(messages):
            return LLMResponse(text="", tool_calls=[call("ask_user", question="Prénom ?").tool_calls[0],
                                                    call("ask_user", question="Nom ?").tool_calls[0]])
        return "Merci : " + " / ".join(r["content"] for r in tool_results(messages))

    fake.script = script(agent)
    cid = new_conversation(user)
    await runner.submit(user, cid, "Présente-toi à moi en me demandant mon identité")
    await wait_status(cid, "waiting_user")
    first = runner.states[cid].ask["question"]
    await runner.submit(user, cid, "Franck" if first == "Prénom ?" else "Olivier")
    await asyncio.sleep(0.1)
    await wait_status(cid, "waiting_user")
    second = runner.states[cid].ask["question"]
    assert {first, second} == {"Prénom ?", "Nom ?"}
    await runner.submit(user, cid, "Olivier" if second == "Nom ?" else "Franck")
    await wait_idle(cid)
    last = json.loads(db.one("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))["data"])
    assert "Franck" in last["content"] and "Olivier" in last["content"]


async def test_context_overflow_is_compacted_not_fatal(fake, user):
    state = {"n": 0}

    def fn(model, system, messages, tools):
        if "contrôleur qualité" in last_user_text(messages) or "Donne un titre" in last_user_text(messages):
            return script(lambda m, t: "")(model, system, messages, tools)
        state["n"] += 1
        if state["n"] == 1:
            return LLMError("prompt is too long", kind="context", status=400)
        return "Réponse après compaction."

    fake.script = fn
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Résume notre échange")
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "done"


async def test_compaction_never_orphans_a_tool_result(fake, user):
    from ely.agent.loop import AgentLoop

    fake.script = script(lambda m, t: "Résumé.")
    cid = new_conversation(user)
    run_id = db.insert("runs", conversation_id=cid, user_id=user["id"], status="running", objective="x", state="{}",
                       created_at=now(), updated_at=now())
    loop = AgentLoop(runner, runner.state(cid, user["id"]), run_id, user)
    history = [
        {"role": "user", "content": "début"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "web_search", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "web_search", "content": "r" * 500},
        {"role": "user", "content": "x" * 400_000},  # énorme message final
    ]
    await loop.compact(history, force=True)
    for i, m in enumerate(history):
        if m["role"] == "tool":
            assert history[i - 1].get("tool_calls"), "résultat d'outil sans son appel"


async def test_resume_after_final_answer_goes_straight_to_verification(fake, user):
    cid = new_conversation(user)
    m1 = save_message(cid, None, {"role": "user", "content": "Donne-moi la capitale du Pérou"}, user["id"])
    run_id = db.insert("runs", conversation_id=cid, user_id=user["id"], status="running", objective="Donne-moi la capitale du Pérou",
                       state=json.dumps({"start_message": m1}), created_at=now(), updated_at=now())
    db.run("UPDATE messages SET run_id = ? WHERE id = ?", (run_id, m1))
    save_message(cid, run_id, {"role": "assistant", "content": "Lima.", "model": "fake:agent"}, user["id"])

    def agent(messages, tools):
        raise AssertionError("le modèle ne doit pas être rappelé sur sa propre réponse")

    fake.script = script(agent)
    await runner.resume_all()
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (run_id,)) == "done"


async def test_empty_replies_do_not_spin_forever(fake, user):
    fake.script = script(lambda m, t: LLMResponse(text=""))
    cid = new_conversation(user)
    res = await runner.submit(user, cid, "Fais quelque chose d'utile")
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (res["run_id"],)) == "error"
    assert sum(1 for c in fake.calls if c["model"] == "agent") <= 3


async def test_verifier_sees_actions_even_after_compaction(fake, user):
    """Le contrôleur lit les actions depuis la base : une compaction ne les lui cache pas."""
    seen = {}

    def fn(model, system, messages, tools):
        text = last_user_text(messages)
        if "contrôleur qualité" in text:
            seen["log"] = text
            return '{"done": true}'
        return script(lambda m, t: call("contacts_save", name="Zoé") if not tool_results(m) else "Ajoutée.")(model, system, messages, tools)

    fake.script = fn
    cid = new_conversation(user)
    await runner.submit(user, cid, "Ajoute Zoé à mes contacts")
    await wait_idle(cid)
    assert "contacts_save" in seen["log"]


@pytest.fixture
def dev_ctx(user):
    return ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop, extra={"selfdev": True})


async def test_rejected_plugin_leaves_no_tool_behind(dev_ctx):
    code = '''from ely.tools import ToolResult, tool

@tool("fantome", "outil de test", {})
async def fantome(ctx):
    return ToolResult("boo")

async def selftest():
    raise RuntimeError("ne marche pas")
'''
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "fantome", "code": code})
    assert r.is_error and "fantome" not in TOOLS


async def test_plugin_overriding_core_tool_is_restored(dev_ctx):
    original = TOOLS["weather"]
    code = '''from ely.tools import ToolResult, tool

@tool("weather", "météo améliorée", {"location": {"type": "string"}}, ["location"])
async def weather(ctx, location):
    return ToolResult("toujours beau")
'''
    r = await execute(dev_ctx, "ely_plugin", {"action": "write", "name": "meteo2", "code": code})
    assert not r.is_error and TOOLS["weather"] is not original
    await execute(dev_ctx, "ely_plugin", {"action": "disable", "name": "meteo2"})
    assert TOOLS["weather"] is original


async def test_cancelled_shell_kills_the_whole_process_group(user):
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    import random

    marker = f"3{random.randint(100, 999)}.{random.randint(10, 99)}"

    def sleepers() -> list[str]:
        ps = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout.splitlines()
        return [line for line in ps if line.split(None, 1)[-1].strip() == f"sleep {marker}"]

    task = asyncio.create_task(execute(ctx, "run_shell", {"command": f"sleep {marker} & sleep {marker}; wait"}))
    await asyncio.sleep(0.6)
    assert len(sleepers()) == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert not sleepers(), f"processus restants : {sleepers()}"


async def test_utc_times_are_converted_to_the_user_timezone(user):
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)
    await execute(ctx, "calendar_add", {"title": "Appel UTC", "start": "2030-03-04T09:00:00Z"})
    ev = db.one("SELECT start FROM events WHERE user_id = ? AND title = 'Appel UTC'", (user["id"],))
    assert ev["start"] == "2030-03-04T10:00"  # Paris = UTC+1 en mars


async def test_deleting_a_user_does_not_break_search_index(user):
    from ely import auth
    from ely.memory.store import purge_user_index

    other = auth.create_user("jetable@x.fr", "Jetable", "motdepasse")
    ocid = new_conversation(other)
    for i in range(3):
        save_message(ocid, None, {"role": "user", "content": f"message jetable {i}"}, other["id"])
    purge_user_index(other["id"])
    db.run("DELETE FROM users WHERE id = ?", (other["id"],))
    cid = new_conversation(user)
    for i in range(3):
        save_message(cid, None, {"role": "user", "content": f"nouveau message {i}"}, user["id"])  # ne doit pas lever


async def test_worktree_is_reset_after_a_rollback(dev_ctx, tmp_path, monkeypatch):
    from ely.selfdev import pipeline

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("A = 'GOOD'\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    monkeypatch.setattr(pipeline, "ROOT", repo)
    monkeypatch.setattr(pipeline, "WORKTREE", tmp_path / "wt")
    good = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    await pipeline.prepare()
    (tmp_path / "wt" / "a.py").write_text("A = 'BAD'\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "bad"], cwd=tmp_path / "wt", check=True)
    subprocess.run(["git", "merge", "-q", "--ff-only", "ely-self"], cwd=repo, check=True)
    subprocess.run(["git", "reset", "-q", "--keep", good], cwd=repo, check=True)  # retour arrière du lanceur
    await pipeline.ensure_session()
    assert (tmp_path / "wt" / "a.py").read_text() == "A = 'GOOD'\n"


async def test_scheduled_task_starts_apart_while_another_is_still_running(fake, user):
    """30/09 : la routine du matin est restée bloquée et celle de midi, programmée dans la même conversation, s'y est
    greffée au lieu de s'exécuter. Une tâche planifiée qui tombe pendant qu'une autre tourne démarre à part."""
    import time

    from ely.scheduler import run_due_schedules

    morning_may_finish = asyncio.Event()

    async def script(model, system, messages, tools):
        text = last_user_text(messages)
        if "contrôleur qualité" in text:
            return '{"done": true, "missing": ""}'
        if "mémoire d'Ely" in text or "Donne un titre" in text or "Résume cet échange" in text:
            return '{"profile": null, "facts": [], "skill": null}'
        if "Routine du matin" in text:
            await morning_may_finish.wait()  # modèle qui ne répond pas
        return "Fait."

    fake.script = script
    cid = new_conversation(user)
    db.insert("schedules", user_id=user["id"], conversation_id=cid, instruction="Routine du matin", cron="0 9 * * *",
                        next_run=time.time() - 1, enabled=1, created_at=now())
    await run_due_schedules()
    await asyncio.sleep(0.2)
    noon = db.insert("schedules", user_id=user["id"], conversation_id=cid, instruction="Routine de midi", cron="0 12 * * *",
                     next_run=time.time() - 1, enabled=1, created_at=now())
    await run_due_schedules()
    run = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        run = db.one("SELECT * FROM runs WHERE objective LIKE ? ORDER BY id DESC LIMIT 1", (f"[Tâche planifiée #{noon}]%",))
        if run and run["status"] == "done":
            break
    assert run and run["status"] == "done" and run["conversation_id"] != cid  # midi s'est fait, à part
    morning_run = db.one("SELECT * FROM runs WHERE conversation_id = ? ORDER BY id LIMIT 1", (cid,))
    assert morning_run["status"] == "running" and "Routine de midi" not in morning_run["objective"]
    morning_may_finish.set()
    await wait_idle(cid)
    assert db.val("SELECT status FROM runs WHERE id = ?", (morning_run["id"],)) == "done"
    assert db.one("SELECT conversation_id FROM schedules WHERE id = ?", (noon,))["conversation_id"] == cid  # la tâche garde sa conversation
