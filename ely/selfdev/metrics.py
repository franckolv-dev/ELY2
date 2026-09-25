"""Mesure des performances d'Ely : la matière première de l'auto-amélioration."""
from __future__ import annotations

import json
import time

from ..db import db
from ..llm.registry import price_of

NEGATIVE = ("ce n'est pas", "c'est pas", "tu t'es trompé", "erreur", "ça ne marche pas", "ca marche pas", "non,", "faux",
            "pas ce que", "recommence", "n'importe quoi", "toujours pas", "tu n'as pas", "t'as pas")


def collect(days: float = 7) -> dict:
    since = time.time() - days * 86400
    runs = db.all("SELECT id, conversation_id, status, objective, steps, state, created_at, updated_at FROM runs WHERE created_at > ?", (since,))
    by_status: dict[str, int] = {}
    rejections = escalations = 0
    durations, steps = [], []
    for r in runs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        st = json.loads(r["state"] or "{}")
        rejections += int(st.get("rejections", 0))
        escalations += int(bool(st.get("escalated")))
        durations.append(r["updated_at"] - r["created_at"])
        steps.append(r["steps"] or 0)
    slow = sorted(runs, key=lambda r: -(r["updated_at"] - r["created_at"]))[:5]
    tools = db.all("SELECT name, COUNT(*) AS calls, SUM(1 - ok) AS errors, AVG(ms) AS avg_ms FROM tool_log WHERE created_at > ? "
                   "GROUP BY name ORDER BY errors DESC, calls DESC", (since,))
    samples = {}
    for t in tools[:10]:
        if t["errors"]:
            samples[t["name"]] = [r["error"] for r in db.all(
                "SELECT error FROM tool_log WHERE name = ? AND ok = 0 AND created_at > ? ORDER BY id DESC LIMIT 3", (t["name"], since))]
    usage = db.all("SELECT model, purpose, SUM(input_tokens) AS inp, SUM(output_tokens) AS out, SUM(cached_tokens) AS cached, "
                   "COUNT(*) AS calls FROM usage WHERE created_at > ? GROUP BY model, purpose ORDER BY inp DESC", (since,))
    cost = 0.0
    for u in usage:
        pi, po = price_of(u["model"])
        cost += ((u["inp"] or 0) - (u["cached"] or 0) * 0.9) / 1e6 * pi + (u["out"] or 0) / 1e6 * po
    complaints = []
    for m in db.all("SELECT m.data, c.title FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                    "WHERE m.role = 'user' AND m.created_at > ? ORDER BY m.id DESC LIMIT 400", (since,)):
        d = json.loads(m["data"])
        text = d.get("content") if isinstance(d.get("content"), str) else ""
        if d.get("kind") != "control" and text and any(n in text.lower() for n in NEGATIVE):
            complaints.append(f"« {m['title']} » : {text[:200]}")
    controls = [json.loads(m["data"]).get("content", "")[:250] for m in db.all(
        "SELECT data FROM messages WHERE role = 'user' AND created_at > ? AND json_extract(data, '$.kind') = 'control' "
        "ORDER BY id DESC LIMIT 15", (since,))]
    return {
        "days": days, "runs": len(runs), "by_status": by_status, "verify_rejections": rejections, "escalations": escalations,
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
        "avg_steps": round(sum(steps) / len(steps), 1) if steps else 0,
        "slowest": [{"run": r["id"], "s": round(r["updated_at"] - r["created_at"]), "steps": r["steps"], "objective": r["objective"][:150]} for r in slow],
        "tools": [{**t, "avg_ms": round(t["avg_ms"] or 0)} for t in tools],
        "tool_error_samples": samples, "usage": usage, "estimated_cost_usd": round(cost, 3),
        "complaints": complaints[:15], "controller_notes": controls,
    }


def report(days: float = 7) -> str:
    d = collect(days)
    lines = [f"# Performances d'Ely sur {d['days']:g} jour(s)",
             f"Tâches : {d['runs']} · statuts : {d['by_status']} · durée moyenne {d['avg_duration_s']} s · {d['avg_steps']} étapes en moyenne",
             f"Refus du contrôleur : {d['verify_rejections']} · escalades : {d['escalations']} · coût estimé ≈ {d['estimated_cost_usd']} $",
             "\n## Outils (appels / erreurs / ms moyens)"]
    lines += [f"- {t['name']} : {t['calls']} / {t['errors']} / {t['avg_ms']}" for t in d["tools"][:25]]
    if d["tool_error_samples"]:
        lines.append("\n## Exemples d'erreurs")
        for name, errs in d["tool_error_samples"].items():
            lines += [f"- {name} : {e[:200]}" for e in errs]
    if d["slowest"]:
        lines.append("\n## Tâches les plus longues")
        lines += [f"- run {s['run']} : {s['s']} s, {s['steps']} étapes — {s['objective']}" for s in d["slowest"]]
    if d["controller_notes"]:
        lines.append("\n## Derniers refus du contrôleur")
        lines += [f"- {c}" for c in d["controller_notes"]]
    if d["complaints"]:
        lines.append("\n## Insatisfactions probables de l'utilisateur")
        lines += [f"- {c}" for c in d["complaints"]]
    if d["usage"]:
        lines.append("\n## Consommation par modèle et usage")
        lines += [f"- {u['model']} [{u['purpose']}] : {u['calls']} appels, {u['inp']} entrée ({u['cached']} en cache), {u['out']} sortie"
                  for u in d["usage"][:15]]
    return "\n".join(lines)
