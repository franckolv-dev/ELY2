"""La boucle d'agent : réfléchir → agir → vérifier → recommencer jusqu'à l'objectif.

Garanties :
- l'agent ne s'arrête que lorsque le contrôleur confirme que l'objectif est atteint
  (ou qu'il n'y a plus aucun progrès possible), pas quand le modèle « pense » avoir fini ;
- les pannes de modèle basculent sur le suivant, puis réessaient avec patience ;
- l'agent piétine → on passe au modèle le plus fort (si configuré) ;
- le contexte est élagué/résumé pour les longues tâches ;
- chaque étape est persistée : un redémarrage reprend là où on en était.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace

from ..auth import tv
from ..config import settings
from ..db import db, now
from ..llm import LLMError, parse_json_loose, registry
from ..llm.base import estimate_tokens, message_text
from ..tools import TOOLS, ToolContext, execute, tools_for
from .prompts import STABLE, dynamic_block
from .runner import ConvState, Runner, public_message, save_message

log = logging.getLogger("ely.agent")

TRIVIAL = re.compile(r"^\W*(salut|bonjour|bonsoir|coucou|hello|hi|hey|merci|thanks|ok|okay|d'accord|dac|super|parfait|top|génial|cool|bravo|bonne nuit|à plus)\b[^?]{0,30}$", re.I)
LOST = "[Résultat perdu (Ely a redémarré pendant l'action) : vérifie si elle a eu lieu avant de la refaire.]"
RETRY_DELAYS = [20, 60, 120, 300, 600]


# ---------------------------------------------------------------------- historique
def load_history(conversation_id: int, pending_ids: set[str] = frozenset()) -> list[dict]:
    conv = db.one("SELECT summary, summary_upto FROM conversations WHERE id = ?", (conversation_id,))
    rows = db.all("SELECT id, data FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id",
                  (conversation_id, conv["summary_upto"] or 0))
    msgs = []
    if conv["summary"]:
        msgs.append({"role": "user", "content": f"[Résumé de la conversation jusqu'ici]\n{conv['summary']}", "_id": conv["summary_upto"]})
    for r in rows:
        m = json.loads(r["data"])
        if m.get("kind") == "note":
            continue
        m["_id"] = r["id"]
        msgs.append(m)
    # les images anciennes coûtent cher : on ne garde que les récentes
    for m in msgs[:-6]:
        if m.get("images"):
            m.pop("images")
            m["content"] = (m.get("content") or "") + " [image omise]"
    return normalize(msgs, pending_ids)


def normalize(msgs: list[dict], pending_ids: set[str] = frozenset()) -> list[dict]:
    """Chaque appel d'outil est suivi de son résultat (résultats manquants synthétisés, orphelins retirés)."""
    results = {m["tool_call_id"]: m for m in msgs if m["role"] == "tool"}
    out: list[dict] = []
    for m in msgs:
        if m["role"] == "tool":
            continue
        out.append(m)
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc["id"] in results:
                    out.append(results.pop(tc["id"]))
                elif tc["id"] not in pending_ids:
                    out.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["name"], "content": LOST, "is_error": True})
    return out


def action_log(history: list[dict], limit_chars: int = 12000) -> str:
    lines = []
    results = {m["tool_call_id"]: m for m in history if m["role"] == "tool"}
    for m in history:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                r = results.get(tc["id"], {})
                args = json.dumps(tc.get("arguments", {}), ensure_ascii=False)[:200]
                status = "ÉCHEC" if r.get("is_error") else "ok"
                lines.append(f"- {tc['name']}({args}) → {status} : {(r.get('content') or '')[:350]}")
        elif m["role"] == "user" and m.get("kind") == "control":
            lines.append(f"- [contrôle] {message_text(m)[:200]}")
    text = "\n".join(lines)
    return text[-limit_chars:] if len(text) > limit_chars else (text or "(aucune action)")


async def verify(objective: str, history: list[dict], answer: str, user_id: int) -> tuple[bool, str]:
    prompt = f"""Tu es le contrôleur qualité d'un agent autonome. Détermine si la DEMANDE de l'utilisateur est entièrement satisfaite.

DEMANDE (et ajouts éventuels) :
{objective[:4000]}

ACTIONS EFFECTUÉES PAR L'AGENT (outils et résultats) :
{action_log(history)}

RÉPONSE FINALE DE L'AGENT :
{answer[:4000]}

Règles :
- Question, conseil ou conversation : satisfaite si la réponse y répond correctement et complètement.
- Action demandée (envoyer, publier, réserver, créer, ajouter, acheter, planifier, rédiger un fichier…) : satisfaite
  uniquement si les résultats d'outils prouvent qu'elle a réellement été effectuée avec succès. Une intention
  (« je vais… »), une simple description ou un brouillon non demandé ne suffit pas.
- Si l'agent pose une question à l'utilisateur : satisfaite seulement si l'information est réellement impossible à obtenir
  autrement (code reçu par SMS, mot de passe inconnu, choix strictement personnel sans défaut raisonnable).
- Si l'agent déclare un blocage : non satisfaite s'il reste des alternatives raisonnables (autre site, navigateur,
  autre méthode, recherche).
Réponds uniquement en JSON : {{"done": true ou false, "missing": "ce qui manque et comment y arriver (1-2 phrases)"}}"""
    for attempt in range(2):
        try:
            raw = await registry.complete(prompt, role="fast", max_tokens=3000, user_id=user_id, purpose="verify")
            v = parse_json_loose(raw)
            return bool(v.get("done", True)), str(v.get("missing", ""))[:600]
        except Exception as e:  # réponse tronquée ou illisible : un nouvel essai, puis on ne bloque jamais l'utilisateur
            log.info("vérification impossible (essai %d) : %s", attempt + 1, e)
    return True, ""


def run_messages(run_id: int) -> list[dict]:
    """Messages complets d'une tâche, depuis la base (indépendant de la compaction du contexte)."""
    return [json.loads(r["data"]) for r in db.all("SELECT data FROM messages WHERE run_id = ? ORDER BY id", (run_id,))]


# ---------------------------------------------------------------------- compaction
def elide(history: list[dict], keep_last: int = 8) -> bool:
    changed = False
    for m in history[:-keep_last]:
        if m["role"] == "tool":
            c = m.get("content") or ""
            if len(c) > 1500:
                m["content"] = c[:700] + "\n[… résultat élagué pour économiser le contexte]"
                changed = True
            if m.get("images"):
                m.pop("images")
                changed = True
    return changed


async def summarize(msgs: list[dict], user_id: int) -> str:
    text = []
    for m in msgs:
        if m["role"] == "tool":
            text.append(f"[résultat {m.get('name')}] {(m.get('content') or '')[:400]}")
        elif m["role"] == "assistant":
            calls = ", ".join(f"{tc['name']}({json.dumps(tc.get('arguments', {}), ensure_ascii=False)[:120]})" for tc in m.get("tool_calls") or [])
            text.append(f"[Ely] {message_text(m)[:1500]}" + (f" → outils : {calls}" if calls else ""))
        else:
            text.append(f"[Utilisateur] {message_text(m)[:2000]}")
    body = "\n".join(text)[-60000:]
    prompt = ("Résume cet échange entre un utilisateur et son agent Ely pour que l'agent puisse continuer sans rien perdre : "
              "demandes, décisions, informations obtenues (noms, dates, adresses, identifiants de rendez-vous, liens, fichiers), "
              "actions réussies, actions échouées et pourquoi, ce qui reste à faire. Sois factuel et dense (max 400 mots).\n\n" + body)
    return await registry.complete(prompt, role="fast", max_tokens=1500, user_id=user_id, purpose="compaction")


# ---------------------------------------------------------------------- boucle
class AgentLoop:
    def __init__(self, runner: Runner, st: ConvState, run_id: int, user: dict) -> None:
        self.runner = runner
        self.st = st
        self.run_id = run_id
        self.user = user
        self.conv_id = st.conversation_id
        run = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        self.run_row = run
        self.state = json.loads(run["state"] or "{}")
        self.conv = db.one("SELECT * FROM conversations WHERE id = ?", (self.conv_id,))
        self.escalated = bool(self.state.get("escalated"))
        self.ask_lock = asyncio.Lock()

    async def emit(self, type_: str, data: dict | None = None) -> None:
        await self.runner.emit(self.st, type_, data)

    def save_state(self, **kw) -> None:
        self.state.update(kw)
        fields = {"state": json.dumps(self.state), "updated_at": now()}
        if "steps" in kw:
            fields["steps"] = kw["steps"]
        db.update("runs", "id = ?", (self.run_id,), **fields)

    def persist(self, msg: dict) -> int:
        clean = {k: v for k, v in msg.items() if not k.startswith("_")}
        mid = save_message(self.conv_id, self.run_id, clean, self.user["id"])
        msg["_id"] = mid
        return mid

    async def publish_message(self, mid: int) -> None:
        await self.emit("message", {"message": public_message(db.one("SELECT * FROM messages WHERE id = ?", (mid,)))})

    # ---------------------------------------------------------------- questions à l'utilisateur
    async def ask(self, question: str, options: list[str] | None, tool_call_id: str) -> str:
        async with self.ask_lock:  # deux ask_user dans le même tour : l'un après l'autre
            return await self._ask(question, options, tool_call_id)

    async def _ask(self, question: str, options: list[str] | None, tool_call_id: str) -> str:
        from ..notify import notify

        self.st.status = "waiting_user"
        self.st.ask = {"question": question, "options": options or [], "tool_call_id": tool_call_id}
        self.st.ask_future = asyncio.get_running_loop().create_future()
        db.run("UPDATE runs SET status = 'waiting_user', updated_at = ? WHERE id = ?", (now(), self.run_id))
        self.save_state(pending_ask=self.st.ask)
        await self.emit("ask_user", self.st.ask)
        asyncio.create_task(notify(self.user["id"], tv(self.user, "Ely a besoin de vous", "Ely a besoin de toi"), question, url=f"/?c={self.conv_id}", tag=f"ask-{self.conv_id}"))
        try:
            return await self.st.ask_future
        finally:
            if not self.runner.shutting_down:  # redémarrage : la question reste en attente et sera reposée
                await self._ask_done()

    async def _ask_done(self) -> None:
        self.st.status = "running"
        self.st.ask = None
        self.st.ask_future = None
        db.run("UPDATE runs SET status = 'running', updated_at = ? WHERE id = ?", (now(), self.run_id))
        self.state.pop("pending_ask", None)
        self.save_state()
        await self.emit("status", {"status": "running"})

    # ---------------------------------------------------------------- outils
    async def run_tool(self, ctx: ToolContext, tc: dict, fail_counts: dict) -> dict:
        t = TOOLS.get(tc["name"])
        label = t.label if t else tc["name"]
        icon = t.icon if t else "⚙️"
        await self.emit("tool_start", {"id": tc["id"], "name": tc["name"], "label": label, "icon": icon,
                                       "args": _preview_args(tc.get("arguments", {}))})
        call_ctx = replace(ctx, extra={**ctx.extra, "tool_call_id": tc["id"]})
        call_ctx.ask = lambda q, o, _id=tc["id"]: self.ask(q, o, _id)
        res = await execute(call_ctx, tc["name"], tc.get("arguments") or {})
        sig = tc["name"] + json.dumps(tc.get("arguments", {}), sort_keys=True, ensure_ascii=False)
        content = res.content
        if res.is_error:
            fail_counts[sig] = fail_counts.get(sig, 0) + 1
            if fail_counts[sig] >= 3:
                content += "\n⚠️ Cette même action a déjà échoué plusieurs fois : change d'approche (autre outil, autre méthode)."
        msg = {"role": "tool", "tool_call_id": tc["id"], "name": tc["name"], "content": content, "is_error": res.is_error}
        if res.images:
            msg["images"] = res.images
        if res.files:
            msg["files"] = res.files
        mid = self.persist(msg)
        await self.publish_message(mid)
        await self.emit("tool_end", {"id": tc["id"], "name": tc["name"], "ok": not res.is_error, "message_id": mid,
                                     "preview": content[:300], "files": res.files})
        return msg

    # ---------------------------------------------------------------- appel modèle
    async def call_model(self, system: list[str], history: list[dict], schemas: list[dict] | None, model: str | None):
        async def on_delta(kind: str, text: str) -> None:
            await self.emit("delta", {"kind": kind, "text": text})

        async def on_switch(ref: str, reason: str) -> None:
            await self.emit("stream_reset", {})
            await self.emit("model", {"model": ref, "reason": reason})

        role = "strong" if self.escalated and registry.resolve("strong") else "main"
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                self.st.partial = ""
                self.st.thinking = ""
                resp = await registry.chat(role=role, model=None if role == "strong" else model, system=system,
                                           messages=[{k: v for k, v in m.items() if not k.startswith("_")} for m in history],
                                           tools=schemas, on_delta=on_delta, user_id=self.user["id"], purpose="agent",
                                           on_switch=on_switch)
                await self.emit("model", {"model": resp.model})
                return resp
            except LLMError as e:
                if e.kind == "context" and attempt < 3:
                    await self.compact(history, force=True)
                    continue
                if attempt >= len(RETRY_DELAYS) or not e.retryable:  # clé refusée, aucun modèle… : inutile d'attendre
                    raise
                delay = RETRY_DELAYS[attempt]
                await self.emit("status", {"status": "retrying", "detail": f"Modèles indisponibles, nouvel essai dans {delay} s : {str(e)[:200]}"})
                await asyncio.sleep(delay)
        raise LLMError("Le modèle n'a pas pu répondre malgré plusieurs essais.")

    async def compact(self, history: list[dict], force: bool = False) -> None:
        try:
            ref = registry.chain("main", self.conv.get("model") or None)[:1]
            ctx_len = registry.info(ref[0]).context if ref else 128_000
        except Exception:
            ctx_len = 128_000
        limit = min(settings.context_soft_limit, int(ctx_len * 0.6))
        if not force and estimate_tokens(history) < limit:
            return
        elide(history)
        if not force and estimate_tokens(history) < limit * 0.8:
            return
        # couper à une frontière propre (jamais sur un résultat d'outil)
        target = int(limit * 0.35)
        cut, acc = len(history), 0
        for i in range(len(history) - 1, 0, -1):
            acc += estimate_tokens([history[i]])
            if acc > target and history[i]["role"] != "tool":
                cut = i
                break
        cut = min(cut, len(history) - 2)
        while cut > 1 and history[cut]["role"] == "tool":
            cut -= 1
        if cut <= 1:
            return
        await self.emit("status", {"status": "running", "detail": "Résumé du contexte…"})
        try:
            summary = await summarize(history[:cut], self.user["id"])
        except Exception as e:
            log.warning("résumé impossible : %s", e)
            return
        upto = max((m.get("_id") or 0) for m in history[:cut])
        db.update("conversations", "id = ?", (self.conv_id,), summary=summary, summary_upto=upto)
        history[:cut] = [{"role": "user", "content": f"[Résumé de la conversation jusqu'ici]\n{summary}", "_id": upto}]

    # ---------------------------------------------------------------- exécution
    async def run(self, resume: bool = False) -> None:
        user = self.user
        objective = self.run_row["objective"]
        pending = self.state.get("pending_ask") if resume else None
        history = load_history(self.conv_id, {pending["tool_call_id"]} if pending else set())

        selfdev = self.conv.get("channel") == "selfdev" and user.get("role") == "admin"
        ctx = ToolContext(user=user, conversation_id=self.conv_id, run_id=self.run_id, emit=self.emit, extra={"selfdev": selfdev})
        tools = tools_for(ctx)
        schemas = [t.schema() for t in tools]
        stable = STABLE
        if selfdev:
            from ..selfdev.tools import SELFDEV_GUIDE
            stable = f"{STABLE}\n\n{SELFDEV_GUIDE}"
        system = [stable, await dynamic_block(user, objective, self.state.get("channel", self.conv.get("channel") or "web"))]
        model = self.conv.get("model") or None
        await self.emit("status", {"status": "running"})

        steps = int(self.run_row["steps"] or 0)
        rejections = int(self.state.get("rejections", 0))
        no_progress = int(self.state.get("no_progress", 0))
        successes = 0
        empty = 0
        fail_counts: dict[str, int] = {}
        # reprise juste après la réponse finale (redémarrage pendant la vérification) : on vérifie directement,
        # sans rappeler le modèle sur un historique qui se termine par sa propre réponse
        final_pending = (resume and not pending and history and history[-1]["role"] == "assistant"
                         and not history[-1].get("tool_calls") and history[-1].get("content"))

        if pending:  # reprise après redémarrage pendant une question à l'utilisateur
            answer = await self.ask(pending["question"], pending.get("options"), pending["tool_call_id"])
            msg = {"role": "tool", "tool_call_id": pending["tool_call_id"], "name": "ask_user", "content": f"Réponse de l'utilisateur : {answer}"}
            self.persist(msg)
            history = load_history(self.conv_id)

        while True:
            if self.st.queue:  # messages ajoutés pendant la tâche
                for q in self.st.queue:
                    history.append({k: v for k, v in q.items()})
                self.st.queue.clear()
                objective = db.val("SELECT objective FROM runs WHERE id = ?", (self.run_id,)) or objective

            if final_pending and not self.st.queue:
                msg = history[-1]
                final_pending = False
            else:
                final_pending = False
                if settings.max_steps and steps >= settings.max_steps:
                    history.append({"role": "user", "kind": "control", "content": "[Limite d'étapes atteinte] N'appelle plus d'outil. "
                                    "Fais le point : ce qui est fait, ce qui reste, et la meilleure suite possible."})
                    resp = await self.call_model(system, history, schemas, model)  # outils déclarés : l'historique en contient
                    msg = resp.to_message()
                    msg.pop("tool_calls", None)
                    msg["content"] = msg["content"] or "J'ai atteint la limite d'étapes pour cette tâche."
                    await self.publish_message(self.persist(msg))
                    db.run("UPDATE runs SET status = 'stopped', updated_at = ? WHERE id = ?", (now(), self.run_id))
                    break

                await self.compact(history)
                resp = await self.call_model(system, history, schemas, model)
                msg = resp.to_message()
                if not msg["content"] and not msg.get("tool_calls"):
                    empty += 1
                    if empty >= 3:
                        raise LLMError("Le modèle renvoie des réponses vides : essaie un autre modèle (menu en haut).")
                    history.append({"role": "user", "kind": "control", "content": "[Contrôle] Ta réponse était vide. Continue la tâche ou donne ta réponse finale."})
                    continue
                empty = 0
                history.append(msg)
                await self.publish_message(self.persist(msg))
                self.st.partial = ""

                if msg.get("tool_calls"):
                    steps += 1
                    results = await asyncio.gather(*(self.run_tool(ctx, tc, fail_counts) for tc in msg["tool_calls"]))
                    history.extend(results)
                    successes += sum(1 for r in results if not r.get("is_error"))
                    self.save_state(steps=steps)
                    continue

            if self.st.queue:
                continue

            answer = msg["content"]
            if not TRIVIAL.match(objective.strip()):
                await self.emit("status", {"status": "running", "detail": "Vérification de l'objectif…"})
                done, missing = await verify(objective, run_messages(self.run_id), answer, user["id"])
                await self.emit("verify", {"done": done, "missing": missing})
                if not done:
                    rejections += 1
                    no_progress = no_progress + 1 if successes == 0 else 1
                    successes = 0
                    self.save_state(rejections=rejections, no_progress=no_progress)
                    if rejections < settings.max_verify_retries and no_progress < 3:
                        if rejections >= 2 and not self.escalated and registry.resolve("strong"):
                            self.escalated = True
                            self.save_state(escalated=True)
                            await self.emit("model", {"model": registry.resolve("strong"), "reason": "escalade vers le modèle le plus fort"})
                        note = {"role": "user", "kind": "control",
                                "content": f"[Contrôle automatique] L'objectif n'est pas encore atteint : {missing}\n"
                                           "Continue sans t'arrêter : agis avec tes outils, change de stratégie si besoin."}
                        history.append(note)
                        await self.publish_message(self.persist(note))
                        continue
            db.run("UPDATE runs SET status = 'done', updated_at = ? WHERE id = ?", (now(), self.run_id))
            break

        asyncio.create_task(self.after_run())

    async def after_run(self) -> None:
        """Titre de la conversation + apprentissage (en arrière-plan, sans bloquer)."""
        from ..memory.learner import learn_from_run, make_title

        try:
            if (self.conv.get("title") or "") in ("", "Nouvelle conversation"):
                title = await make_title(self.conv_id, self.user["id"])
                if title:
                    db.update("conversations", "id = ?", (self.conv_id,), title=title)
                    await self.emit("title", {"title": title})
        except Exception as e:
            log.info("titre : %s", e)
        if self.state.get("channel") == "schedule":  # tâche planifiée : on prévient l'utilisateur du résultat
            from ..notify import notify

            last = db.one("SELECT data FROM messages WHERE run_id = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1", (self.run_id,))
            text = message_text(json.loads(last["data"])) if last else "Tâche terminée."
            await notify(self.user["id"], "Ely · tâche planifiée", text[:400], url=f"/?c={self.conv_id}")
        try:
            await learn_from_run(self.user, self.conv_id, self.run_id)
        except Exception as e:
            log.info("apprentissage : %s", e)


def _preview_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if k in ("password",):
            s = "••••"
        parts.append(f"{k}={s[:80]}")
    return ", ".join(parts)[:240]


# ---------------------------------------------------------------------- sous-agents
async def run_subagent(parent: ToolContext, task: str, context: str, idx: int, max_steps: int = 40) -> str:
    async def emit(type_: str, data: dict) -> None:
        if type_ in ("tool_start", "tool_end", "browser_frame"):
            await parent.emit(type_, {**data, "sub": idx})

    ctx = ToolContext(user=parent.user, conversation_id=parent.conversation_id, run_id=parent.run_id, emit=emit,
                      ask=None, depth=1, extra={"browser_key": f"sub-{idx}"})
    tools = tools_for(ctx)
    schemas = [t.schema() for t in tools]
    system = [STABLE + "\n\n# Rôle\nTu es un sous-agent : accomplis ta sous-tâche de façon autonome puis rends un rapport "
                       "factuel, complet et structuré (c'est tout ce que l'agent principal verra).",
              await dynamic_block(parent.user, task)]
    history: list[dict] = [{"role": "user", "content": (f"Contexte : {context}\n\n" if context else "") + f"Ta sous-tâche : {task}"}]
    last = ""
    for _ in range(max_steps):
        resp = await registry.chat(role="main", system=system, messages=history, tools=schemas, user_id=parent.user_id, purpose="subagent")
        msg = resp.to_message()
        history.append(msg)
        last = msg["content"] or last
        if not msg.get("tool_calls"):
            return last or "(rapport vide)"
        for tc in msg["tool_calls"]:
            t = TOOLS.get(tc["name"])
            await emit("tool_start", {"id": tc["id"], "name": tc["name"], "label": t.label if t else tc["name"],
                                      "icon": t.icon if t else "⚙️", "args": _preview_args(tc.get("arguments", {}))})
        results = await asyncio.gather(*(execute(ctx, tc["name"], tc.get("arguments") or {}) for tc in msg["tool_calls"]))
        for tc, res in zip(msg["tool_calls"], results):
            await emit("tool_end", {"id": tc["id"], "name": tc["name"], "ok": not res.is_error, "preview": res.content[:200]})
            m = {"role": "tool", "tool_call_id": tc["id"], "name": tc["name"], "content": res.content, "is_error": res.is_error}
            if res.images:
                m["images"] = res.images
            history.append(m)
        elide(history, keep_last=6)
    return f"(limite d'étapes atteinte) {last}"
