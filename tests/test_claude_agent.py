"""Auto-amélioration confiée à Claude (Agent SDK) : mission dans la copie de travail, garde des outils, redémarrage
à la fin seulement, repli si Claude ne répond pas, jamais de mission relancée à l'aveugle.

Le SDK n'est jamais appelé : un faux `_run_sdk` joue la mission en se servant, comme le vrai, de la garde et des
outils d'Ely que la session lui confie."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from conftest import new_conversation, wait_idle
from test_agent import model_notices, routed, with_strong_model

from ely.agent.runner import runner
from ely.config import settings
from ely.db import db, now
from ely.llm import claude_agent, registry
from ely.selfdev import pipeline
from ely.selfdev.tools import CLAUDE_TOOLS, claude_session, start_session
from ely.tools import ToolContext

OPUS = "claude:claude-opus-5-5"


async def _noop(*a, **k):
    pass


@pytest.fixture
def claude(monkeypatch):
    """Claude relié (jeton de Claude Code, SDK installé) ; box["script"](session) joue la mission."""
    monkeypatch.setattr(settings, "claude_code_oauth_token", "jeton-de-test")
    monkeypatch.setattr(claude_agent, "sdk_version", lambda: "0.2.162")
    db.set_setting("model_selfdev", "auto")
    box = {"script": None, "sessions": []}

    async def fake_sdk(session):
        box["sessions"].append(session)
        async for ev in box["script"](session):
            yield ev

    monkeypatch.setattr(claude_agent, "_real_run_sdk", claude_agent._run_sdk, raising=False)
    monkeypatch.setattr(claude_agent, "_run_sdk", fake_sdk)
    return box


def messages(cid: int) -> list[dict]:
    return [json.loads(r["data"]) for r in db.all("SELECT data FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))]


def last_run(cid: int) -> dict:
    return db.one("SELECT * FROM runs WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (cid,))


async def test_self_improvement_is_entrusted_to_claude(fake, user, fake_repo, claude, monkeypatch):
    """Claude corrige le code dans la copie, lance les tests et déploie avec les outils d'Ely. Le compte rendu arrive
    dans la conversation, la consommation est notée, et Ely ne redémarre qu'une fois la mission close."""
    from ely.selfdev import github

    published = []

    async def publish(summary, sha):
        published.append((summary, sha))
        return "https://github.com/example/ely/pull/1"

    monkeypatch.setattr(github, "publish_improvement", publish)
    restarts, seen = [], {}
    monkeypatch.setenv("ELY_SUPERVISED", "1")
    monkeypatch.setattr(pipeline, "RESTART_DELAY", 0)
    monkeypatch.setattr(pipeline, "request_restart", lambda: restarts.append(last_run(seen["cid"])["status"]))

    async def mission(s):
        assert s.model_id == "claude-opus-5-5" and s.cwd == pipeline.WORKTREE and "Bash" not in s.tools
        yield {"type": "text", "text": "Je corrige add()."}
        target = s.cwd / "app.py"
        assert claude_agent.check(s, "Edit", {"file_path": str(target)}) is None
        yield {"type": "tool_start", "id": "t1", "name": "Edit", "input": {"file_path": str(target)}}
        target.write_text(target.read_text().replace("a - b", "a + b"))
        yield {"type": "tool_end", "id": "t1", "name": "Edit", "ok": True, "content": "ok"}
        for i, (name, args) in enumerate((("ely_test", {}), ("ely_deploy", {"summary": "corrige add"}))):
            yield {"type": "tool_start", "id": f"m{i}", "name": f"mcp__ely__{name}", "input": args}
            r = await s.call(name, args)
            assert not r.is_error, r.content
            yield {"type": "tool_end", "id": f"m{i}", "name": f"mcp__ely__{name}", "ok": True, "content": r.content}
        await asyncio.sleep(0.05)
        assert not restarts  # déployé, mais pas de redémarrage au milieu de la mission
        yield {"type": "result", "ok": True, "text": "Compte rendu : add() additionne de nouveau, correctif déployé.",
               "error": "", "cost": 0.42, "turns": 6, "session_id": "s1", "input_tokens": 12000, "output_tokens": 800,
               "cached_tokens": 9000}

    claude["script"] = mission
    fake.script, used = routed(lambda m, t: "Je ne devrais pas travailler ici.")
    q = runner.subscribe(user["id"])
    try:
        cid = seen["cid"] = start_session(user, "Corrige l'addition")
        await wait_idle(cid, timeout=120)
        await asyncio.sleep(0.1)
    finally:
        runner.unsubscribe(user["id"], q)
    assert used == []  # aucun autre modèle n'a travaillé
    assert "return a + b" in (fake_repo / "app.py").read_text()  # version active corrigée
    assert published == [("corrige add", await pipeline.head())]
    last = messages(cid)[-1]
    assert last["content"].startswith("Compte rendu") and last["model"] == OPUS
    assert last_run(cid)["status"] == "done" and restarts == ["done"]
    assert db.one("SELECT * FROM usage WHERE user_id = ? AND model = ? AND purpose = 'selfdev' AND input_tokens = 12000",
                  (user["id"], OPUS))
    labels = []
    while not q.empty():
        ev = q.get_nowait()
        if ev["type"] == "tool_start":
            labels.append(ev["label"])
    assert labels == ["Modification du code", "Tests", "Déploiement"]


async def test_claude_stays_in_the_working_copy(user, fake_repo):
    """Les outils natifs de Claude n'atteignent que la copie de travail, hors secrets et données ; rien d'autre."""
    ctx = ToolContext(user=user, conversation_id=0, run_id=0, emit=_noop, extra={"selfdev": True})
    s = claude_session(ctx, "Améliore-toi", OPUS)
    wt = pipeline.WORKTREE
    for name, args in (("Edit", {"file_path": str(wt / "ely" / "loop.py")}), ("Write", {"file_path": "tests/test_neuf.py"}),
                       ("Read", {"file_path": "app.py"}), ("Grep", {"pattern": "def ", "path": "ely"}),
                       ("Glob", {"pattern": "**/*.py"}), ("mcp__ely__ely_test", {}), ("mcp__ely__ely_deploy", {}),
                       ("mcp__ely__ely_journal", {"action": "list"})):
        assert claude_agent.check(s, name, args) is None, (name, args)
    for name, args in (("Edit", {"file_path": str(fake_repo / "app.py")}),  # la version active
                       ("Edit", {"file_path": "../repo/app.py"}), ("Read", {"file_path": str(wt / ".env")}),
                       ("Write", {"file_path": "data/ely.db"}), ("Write", {"file_path": ".claude/settings.json"}),
                       ("Glob", {"pattern": "/etc/*"}),
                       ("Grep", {"pattern": "KEY", "path": "~/.ssh"}), ("Bash", {"command": "rm -rf /"}),
                       ("WebFetch", {"url": "https://example.com"}), ("mcp__ely__browser", {"action": "open"})):
        assert claude_agent.check(s, name, args), (name, args)


async def test_sdk_receives_the_guard_and_one_credential(user, fake_repo, monkeypatch):
    """Ce que reçoit le vrai SDK : Opus, les outils natifs choisis, les outils d'Ely par MCP, la garde dans la
    permission et dans le crochet PreToolUse, le budget des réglages, et une seule source d'identifiants."""
    sdk = pytest.importorskip("claude_agent_sdk")
    monkeypatch.setattr(settings, "claude_code_oauth_token", "jeton")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-cle")
    db.set_setting("claude_budget", 3)
    ctx = ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop, extra={"selfdev": True})
    opts = claude_agent.options(claude_session(ctx, "Améliore-toi", OPUS))
    db.set_setting("claude_budget", claude_agent.DEFAULT_BUDGET)
    assert opts.env["CLAUDE_CODE_OAUTH_TOKEN"] == "jeton" and opts.env["ANTHROPIC_API_KEY"] == ""
    assert opts.model == "claude-opus-5-5" and opts.tools == CLAUDE_TOOLS and opts.max_budget_usd == 3
    assert opts.allowed_tools and all(t.startswith("mcp__ely__") for t in opts.allowed_tools)
    assert opts.setting_sources == ["project"] and opts.strict_mcp_config
    assert isinstance(await opts.can_use_tool("Edit", {"file_path": "/etc/hosts"}, None), sdk.PermissionResultDeny)
    assert isinstance(await opts.can_use_tool("Edit", {"file_path": "app.py"}, None), sdk.PermissionResultAllow)
    hook = opts.hooks["PreToolUse"][0].hooks[0]
    out = await hook({"tool_name": "Read", "tool_input": {"file_path": str(pipeline.WORKTREE / ".env")}}, "t1", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await hook({"tool_name": "Read", "tool_input": {"file_path": "app.py"}}, "t2", None) == {}
    bridged = {t.name: t for t in claude_agent.bridge_tools(claude_session(ctx, "x", OPUS))}
    r = await bridged["ely_metrics"].handler({"days": 1})
    assert "Performances d'Ely" in r["content"][0]["text"] and not r["is_error"]
    monkeypatch.setattr(settings, "claude_code_oauth_token", "")
    assert claude_agent.credentials() == {"ANTHROPIC_API_KEY": "sk-ant-cle", "CLAUDE_CODE_OAUTH_TOKEN": ""}


async def test_claude_unavailable_hands_over_to_the_strong_model(fake, user, fake_repo, claude):
    """Claude ne répond pas (CLI absent, jeton refusé…) avant d'avoir agi : la session continue sur le modèle
    d'escalade, et Ely le dit."""
    with_strong_model(fake)

    async def mission(s):
        raise RuntimeError("Claude Code introuvable")
        yield  # pragma: no cover

    claude["script"] = mission
    fake.script, used = routed(lambda m, t: "Rien à améliorer aujourd'hui.")
    q = runner.subscribe(user["id"])
    try:
        cid = start_session(user, "Sois plus rapide sur Doctolib")
        await wait_idle(cid)
    finally:
        runner.unsubscribe(user["id"], q)
    assert used == ["fort"], used
    assert any(f"{OPUS} indisponible" in n and "Claude Code introuvable" in n and "bascule sur fake:fort" in n
               for n in model_notices(q))
    assert last_run(cid)["status"] == "done" and messages(cid)[-1]["content"] == "Rien à améliorer aujourd'hui."


async def test_claude_failure_after_acting_is_not_replayed(fake, user, fake_repo, claude):
    """Claude a déjà modifié la copie quand la mission casse : aucun autre modèle ne reprend à l'aveugle, l'erreur
    est rapportée et la copie reste en l'état."""
    with_strong_model(fake)

    async def mission(s):
        yield {"type": "tool_start", "id": "t1", "name": "Edit", "input": {"file_path": "app.py"}}
        (s.cwd / "app.py").write_text("def add(a, b):\n    return b + a\n")
        yield {"type": "tool_end", "id": "t1", "name": "Edit", "ok": True, "content": "ok"}
        raise ConnectionError("connexion coupée")

    claude["script"] = mission
    fake.script, used = routed(lambda m, t: "Je reprends.")
    cid = start_session(user, "Corrige l'addition")
    await wait_idle(cid)
    assert used == []
    run = last_run(cid)
    assert run["status"] == "error" and "connexion coupée" in run["error"]
    note = messages(cid)[-1]["content"]
    assert "connexion coupée" in note and "relancez l'auto-amélioration" in note
    assert "b + a" in (pipeline.WORKTREE / "app.py").read_text() and "a - b" in (fake_repo / "app.py").read_text()


async def test_interrupted_claude_mission_is_not_restarted(fake, user, claude):
    """Ely redémarre pendant une mission Claude : au retour, la mission n'est pas relancée (son état est inconnu),
    Ely l'explique."""
    async def mission(s):
        raise AssertionError("la mission ne doit pas être relancée")
        yield  # pragma: no cover

    claude["script"] = mission
    cid = db.insert("conversations", user_id=user["id"], title="🛠️ Auto-amélioration", channel="selfdev",
                    created_at=now(), updated_at=now())
    run_id = db.insert("runs", conversation_id=cid, user_id=user["id"], status="running", objective="Améliore-toi",
                       state=json.dumps({"channel": "selfdev", "claude": OPUS}), created_at=now(), updated_at=now())
    runner._start(runner.state(cid, user["id"]), run_id, user, resume=True)
    await wait_idle(cid)
    assert claude["sessions"] == []
    assert last_run(cid)["status"] == "stopped"
    assert "interrompue par un redémarrage" in messages(cid)[-1]["content"]


def test_claude_is_chosen_by_default_only_with_its_token(fake, monkeypatch):
    """Auto-amélioration en automatique : Opus avec le jeton de Claude Code ; avec une clé d'API seule (payante au
    token), seulement si l'administrateur le choisit. Les autres rôles ne changent pas."""
    with_strong_model(fake)
    db.set_setting("model_selfdev", "auto")
    monkeypatch.setattr(claude_agent, "sdk_version", lambda: "0.2.162")
    monkeypatch.setattr(settings, "claude_code_oauth_token", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-cle")
    assert registry.resolve("selfdev") == "fake:fort"
    db.set_setting("model_selfdev", "claude:claude-fable-5-1")
    assert registry.resolve("selfdev") == "claude:claude-fable-5-1"
    db.set_setting("model_selfdev", "auto")
    monkeypatch.setattr(settings, "claude_code_oauth_token", "jeton")
    assert registry.roles_view()["selfdev"]["effective"] == OPUS
    assert registry.resolve("main") == "fake:agent" and registry.resolve("strong") == "fake:fort"
    monkeypatch.setattr(claude_agent, "sdk_version", lambda: "")  # SDK pas installé
    assert registry.resolve("selfdev") == "fake:fort"


async def test_claude_settings_api(user, claude):
    """Réglages → Modèles → Claude : état, essai des identifiants, budget par mission (administrateur seulement)."""
    from ely.api.app import create_app

    async def pong(s):
        assert s.tools == [] and s.bridge == []
        yield {"type": "result", "ok": True, "text": "OK", "error": "", "cost": 0.001}

    claude["script"] = pong
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://ely.test") as c:
        r = await c.post("/api/auth/login", json={"email": user["email"], "password": "motdepasse"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        st = (await c.get("/api/admin/claude", headers=h)).json()
        assert st["ready"] and st["source"] == "token" and OPUS in [m["ref"] for m in st["models"]]
        r = (await c.post("/api/admin/claude/test", headers=h, json={})).json()
        assert r["ok"] and r["text"] == "OK"
        assert (await c.put("/api/admin/claude", headers=h, json={"budget": 2.5})).json()["budget"] == 2.5
        assert claude_agent.budget() == 2.5
        await c.put("/api/admin/claude", headers=h, json={"budget": claude_agent.DEFAULT_BUDGET})


async def test_usage_limit_reached_hands_over_to_the_strong_model(fake, user, fake_repo, claude, monkeypatch):
    """Quota du forfait atteint dès le départ : le CLI renvoie une erreur d'API (429) sans avoir rien fait. La
    session passe sur le modèle d'escalade, avec la raison lisible dans l'annonce."""
    sdk = pytest.importorskip("claude_agent_sdk")
    limit = "Claude AI usage limit reached · resets 6pm"

    async def query(prompt, options):
        yield sdk.AssistantMessage(content=[sdk.TextBlock(text=f"API Error: 429 {limit}")], model="<synthetic>", error="rate_limit")
        yield sdk.ResultMessage(subtype="success", duration_ms=5, duration_api_ms=0, is_error=True, num_turns=1,
                                session_id="s1", result=f"API Error: 429 {limit}", api_error_status=429, total_cost_usd=0)

    monkeypatch.setattr(claude_agent, "_run_sdk", claude_agent._real_run_sdk)  # le vrai adaptateur, SDK simulé
    monkeypatch.setattr(sdk, "query", query)
    with_strong_model(fake)
    fake.script, used = routed(lambda m, t: "Leçon ajoutée : vérifier l'adresse du cabinet.")
    q = runner.subscribe(user["id"])
    try:
        cid = start_session(user, "Améliore-toi")
        await wait_idle(cid)
    finally:
        runner.unsubscribe(user["id"], q)
    assert used == ["fort"], used
    notices = model_notices(q)
    assert any(limit in n and "bascule sur fake:fort" in n for n in notices), notices
    assert last_run(cid)["status"] == "done"
    # le même quota en pleine mission est reconnu comme tel par l'adaptateur
    events = [ev async for ev in claude_agent._real_run_sdk(claude_agent.Session(prompt="x", cwd=fake_repo))]
    assert events[-1]["type"] == "result" and events[-1]["limit"] and not events[-1]["ok"]


async def test_quota_reached_mid_mission_restarts_it_without_claude(fake, user, fake_repo, claude):
    """Le quota tombe alors que Claude a déjà modifié le code : Ely s'arrête, consigne son travail dans un fichier
    markdown, puis recommence la mission avec le modèle d'escalade, qui sait ce que Claude a déjà mis en place."""
    with_strong_model(fake)

    async def mission(s):
        yield {"type": "text", "text": "L'addition est fausse, je la corrige."}
        yield {"type": "tool_start", "id": "t1", "name": "Edit", "input": {"file_path": str(s.cwd / "app.py")}}
        (s.cwd / "app.py").write_text("def add(a, b):\n    return a + b\n")
        yield {"type": "tool_end", "id": "t1", "name": "Edit", "ok": True, "content": "ok"}
        yield {"type": "result", "ok": False, "limit": True, "text": "", "error": "Claude AI usage limit reached · resets 6pm",
               "input_tokens": 5000, "output_tokens": 300, "cached_tokens": 0}

    seen = {}

    def astra(messages, tools):
        seen["mission"] = next(m["content"] for m in messages if m["role"] == "user" and "Mission relancée" in str(m["content"]))
        return "Le correctif de Claude était bon ; tests verts, rien d'autre à faire."

    claude["script"] = mission
    fake.script, used = routed(astra)
    cid = start_session(user, "Corrige l'addition")
    for _ in range(200):
        await asyncio.sleep(0.05)
        runs = db.all("SELECT * FROM runs WHERE conversation_id = ? ORDER BY id", (cid,))
        if len(runs) == 2 and runs[1]["status"] == "done":
            break
    assert [r["status"] for r in runs] == ["stopped", "done"]
    assert len(claude["sessions"]) == 1 and used == ["fort"]  # Claude n'est pas rappelé, Astra refait la mission
    note = next(m for m in messages(cid) if m.get("kind") == "note")["content"]
    report = note.split("Fichiers → ")[1].split(". ")[0]
    text = (settings.user_dir(user["id"]) / "files" / report).read_text()
    assert "Corrige l'addition" in text and "usage limit reached" in text and "Modification du code" in text
    assert "app.py" in text and "+    return a + b" in text  # modifications non déployées, diff compris
    assert "Corrige l'addition" in seen["mission"] and report in seen["mission"] and "ely_code action=diff" in seen["mission"]
    assert next(m for m in messages(cid) if m.get("kind") == "relaunch")["content"] == seen["mission"]
    assert "a - b" in (fake_repo / "app.py").read_text()  # rien n'a été déployé
