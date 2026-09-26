"""Outils : agenda, contacts, fichiers, code, planification, mémoire, identifiants, navigateur."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from conftest import new_conversation

from ely.db import db
from ely.tools import ToolContext, execute


async def _noop(*a, **k):
    pass


@pytest.fixture
def ctx(user):
    return ToolContext(user=user, conversation_id=new_conversation(user), run_id=0, emit=_noop)


async def test_unknown_tool_and_missing_args(ctx):
    r = await execute(ctx, "nope", {})
    assert r.is_error and "inconnu" in r.content
    r = await execute(ctx, "calendar_add", {"title": "x"})
    assert r.is_error and "start" in r.content
    r = await execute(ctx, "calendar_add", {"_invalid": "{"})
    assert r.is_error


async def test_calendar_internal_and_ics(ctx):
    r = await execute(ctx, "calendar_add", {"title": "Kiné", "start": "2031-05-06T09:30", "location": "Lyon"})
    assert not r.is_error, r.content
    r = await execute(ctx, "calendar_list", {"start": "2031-05-06", "end": "2031-05-07"})
    assert "Kiné" in r.content
    eid = r.content.split("[")[1].split("]")[0]
    r = await execute(ctx, "calendar_update", {"id": eid, "start": "2031-05-06T11:00", "end": "2031-05-06T12:00"})
    assert not r.is_error
    from ely.tools.pim import ics_feed

    feed = ics_feed(ctx.user_id)
    assert "SUMMARY:Kiné" in feed and "BEGIN:VALARM" in feed
    r = await execute(ctx, "calendar_update", {"id": eid, "delete": True})
    assert "supprimé" in r.content


async def test_contacts_internal(ctx):
    await execute(ctx, "contacts_save", {"name": "Marie Curie", "email": "marie@exemple.fr", "phone": "0611223344"})
    r = await execute(ctx, "contacts_search", {"query": "curie"})
    assert "marie@exemple.fr" in r.content
    cid = r.content.split("[")[1].split("]")[0]
    await execute(ctx, "contacts_save", {"id": cid, "company": "Institut du Radium"})
    r = await execute(ctx, "contacts_search", {"query": "radium"})
    assert "Marie Curie" in r.content


async def test_files_and_python(ctx):
    r = await execute(ctx, "file_write", {"path": "notes/a.txt", "content": "bonjour"})
    assert r.files == ["notes/a.txt"]
    r = await execute(ctx, "file_read", {"path": "notes/a.txt"})
    assert r.content == "bonjour"
    r = await execute(ctx, "file_read", {"path": "../../etc/passwd"})
    assert r.is_error
    code = "import docx\nd = docx.Document(); d.add_paragraph('Rapport'); d.save('rapport.docx')\nprint(6*7)"
    r = await execute(ctx, "run_python", {"code": code})
    assert "42" in r.content and "rapport.docx" in r.files, r.content
    r = await execute(ctx, "file_read", {"path": "rapport.docx"})
    assert "Rapport" in r.content
    r = await execute(ctx, "run_python", {"code": "raise SystemExit(3)"})
    assert r.is_error


async def test_long_output_is_spilled(ctx):
    r = await execute(ctx, "run_python", {"code": "print('x' * 60000)"})
    assert "tronquée" in r.content and len(r.content) < 30000


async def test_schedule_and_scheduler(fake, ctx):
    r = await execute(ctx, "schedule", {"action": "create", "instruction": "Dis bonjour", "cron": "0 8 * * 1-5"})
    assert "planifiée" in r.content, r.content
    r = await execute(ctx, "schedule", {"action": "create", "instruction": "Rappel", "when": "2000-01-01T10:00"})
    assert r.is_error
    r = await execute(ctx, "schedule", {"action": "list"})
    assert "Dis bonjour" in r.content
    sid = int(r.content.split("#")[1].split()[0])
    # une tâche échue est lancée par le planificateur
    fake.script = lambda **kw: "Bonjour !"
    db.run("UPDATE schedules SET next_run = 0 WHERE id = ?", (sid,))
    from ely.scheduler import run_due_schedules

    await run_due_schedules()
    row = db.one("SELECT * FROM schedules WHERE id = ?", (sid,))
    assert row["next_run"] > 0 and row["last_status"] == "lancée"
    await asyncio.sleep(0.3)


async def test_rephrased_schedule_updates_the_task_instead_of_duplicating_it(ctx):
    """« Chaque matin à 9 h… » redemandé, précisé ou reformulé : une seule tâche, mise à jour."""
    def active():
        return db.all("SELECT * FROM schedules WHERE user_id = ? AND enabled = 1", (ctx.user_id,))

    r = await execute(ctx, "schedule", {"action": "create", "instruction": "Résumé de mes e-mails et de l'agenda", "cron": "0 9 * * *"})
    assert "planifiée" in r.content, r.content
    sid = active()[0]["id"]
    r = await execute(ctx, "schedule", {"action": "create", "cron": "0  9 * * *",
                                        "instruction": "Chaque matin : e-mails, agenda, actualités, envoi sur Telegram"})
    assert r.is_error and f"#{sid}" in r.content and "update" in r.content
    assert len(active()) == 1
    r = await execute(ctx, "schedule", {"action": "update", "id": sid,
                                        "instruction": "Chaque matin : e-mails, agenda, actualités, envoi sur Telegram"})
    assert not r.is_error, r.content
    [row] = active()
    assert "Telegram" in row["instruction"] and row["cron"] == "0 9 * * *"
    # une vraie autre tâche au même horaire reste possible
    r = await execute(ctx, "schedule", {"action": "create", "instruction": "Arroser les plantes", "cron": "0 9 * * *", "distinct": True})
    assert not r.is_error and len(active()) == 2
    # changer l'horaire d'une tâche existante
    r = await execute(ctx, "schedule", {"action": "update", "id": sid, "cron": "30 8 * * 1-5"})
    assert not r.is_error and db.one("SELECT cron FROM schedules WHERE id = ?", (sid,))["cron"] == "30 8 * * 1-5"
    r = await execute(ctx, "schedule", {"action": "update", "id": 999999, "instruction": "x"})
    assert r.is_error


async def test_memory_tools(ctx):
    await execute(ctx, "remember", {"fact": "Le médecin traitant de Franck est le Dr Martin à Villeurbanne", "category": "santé"})
    r = await execute(ctx, "remember", {"fact": "Le médecin traitant de Franck est le Dr Martin à Villeurbanne (Doctolib)", "category": "santé"})
    assert "mis à jour" in r.content  # dédoublonnage
    r = await execute(ctx, "recall", {"query": "médecin"})
    assert "Dr Martin" in r.content
    import re

    mid = int(re.search(r"#(\d+) \[", r.content).group(1))
    r = await execute(ctx, "forget", {"memory_id": mid})
    assert not r.is_error


async def test_skills_are_injected_when_relevant(ctx):
    from ely.agent.prompts import dynamic_block

    await execute(ctx, "skill_save", {"name": "Prendre rendez-vous sur Doctolib", "description": "Réserver un rendez-vous médical sur Doctolib",
                                      "content": "1. Ouvrir doctolib.fr\n2. Chercher le praticien"})
    block = await dynamic_block(ctx.user, "Prends-moi un rendez-vous chez le dentiste sur Doctolib")
    assert "Chercher le praticien" in block
    block = await dynamic_block(ctx.user, "Quelle heure est-il à Tokyo ?")
    assert "Chercher le praticien" not in block


async def test_credentials(ctx):
    await execute(ctx, "credentials", {"action": "save", "service": "Doctolib", "username": "f@x.fr", "password": "s3cret"})
    r = await execute(ctx, "credentials", {"action": "get", "service": "doctolib"})
    assert "s3cret" in r.content
    r = await execute(ctx, "credentials", {"action": "get", "service": "impots"})
    assert r.is_error


async def test_email_without_account_gives_guidance(ctx):
    r = await execute(ctx, "email_send", {"to": "a@b.fr", "subject": "x", "body": "y"})
    assert r.is_error and "Connexions" in r.content


@pytest.mark.skipif(not os.environ.get("ELY_BROWSER_EXECUTABLE") and not Path.home().joinpath(".cache/ms-playwright").exists(),
                    reason="Chromium absent")
async def test_browser_fills_a_form(ctx, tmp_path):
    page = tmp_path / "rdv.html"
    page.write_text("""<html><head><title>Prise de RDV</title></head><body>
      <div id="didomi-host"><button onclick="this.parentNode.remove()">Tout accepter</button></div>
      <h1>Dr Martin</h1><label for="n">Nom</label><input id="n" required>
      <select id="m"><option>Consultation</option><option>Suivi</option></select>
      <button onclick="document.body.innerHTML='<h1>Rendez-vous confirmé pour '+document.getElementById('n').value+' ('+document.getElementById('m').value+')</h1>'">Réserver</button>
    </body></html>""")
    frames = []

    async def emit(t, d):
        frames.append(t)

    ctx.emit = emit
    r = await execute(ctx, "browser", {"action": "open", "url": page.as_uri()})
    assert not r.is_error, r.content
    assert "bandeau cookies fermé" in r.content
    lines = r.content.splitlines()
    ref_name = next(l for l in lines if 'champ(text) "Nom"' in l).split("]")[0][1:]
    ref_sel = next(l for l in lines if "liste" in l).split("]")[0][1:]
    ref_btn = next(l for l in lines if 'bouton "Réserver"' in l).split("]")[0][1:]
    await execute(ctx, "browser", {"action": "type", "ref": int(ref_name), "text": "Franck"})
    await execute(ctx, "browser", {"action": "select", "ref": int(ref_sel), "text": "Suivi"})
    r = await execute(ctx, "browser", {"action": "click", "ref": int(ref_btn)})
    assert "Rendez-vous confirmé pour Franck (Suivi)" in r.content, r.content
    r = await execute(ctx, "browser", {"action": "screenshot"})
    assert r.images and "browser_frame" in frames
    from ely.browser import manager

    await manager.shutdown()


async def test_seed_skills_match_real_requests(ctx):
    from ely.memory.seed import seed_skills
    from ely.memory.store import relevant_skills

    seed_skills()
    assert seed_skills() == 0  # idempotent
    hits = relevant_skills(ctx.user_id, "Prends-moi rendez-vous chez le dentiste sur Doctolib jeudi")
    assert hits and "Doctolib" in hits[0]["name"]
    hits = relevant_skills(ctx.user_id, "Publie un post sur LinkedIn à propos de notre catalogue")
    assert hits and "LinkedIn" in hits[0]["name"]
    assert not relevant_skills(ctx.user_id, "Quelle est la capitale du Japon ?")


async def test_seed_skills_read_email_codes_instead_of_asking():
    from ely.db import db
    from ely.memory.seed import seed_skills

    seed_skills()
    row = db.one("SELECT id, content FROM skills WHERE name = 'Prendre un rendez-vous médical sur Doctolib'")
    # base d'une version précédente : le code reçu par e-mail était demandé à l'utilisateur
    old = row["content"].split("   Code reçu par e-mail")[0] + "   Code reçu par SMS ou e-mail → ask_user (c'est le seul cas où demander).\n5. suite"
    db.run("UPDATE skills SET content = ? WHERE id = ?", (old, row["id"]))
    seed_skills()
    content = db.val("SELECT content FROM skills WHERE id = ?", (row["id"],))
    assert "ask_user (c'est le seul cas" in content and "SMS ou e-mail → ask_user" not in content
    assert "le lire dans la messagerie" in content and content.endswith("5. suite")
    assert db.one("SELECT rowid FROM skills_fts WHERE skills_fts MATCH 'messagerie' AND rowid = ?", (row["id"],))
