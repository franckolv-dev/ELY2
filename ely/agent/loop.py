"""La boucle d'agent : réfléchir → agir → vérifier → recommencer jusqu'à l'objectif.

Garanties :
- l'agent ne s'arrête que lorsque le contrôleur confirme que l'objectif est atteint
  (ou qu'il n'y a plus aucun progrès possible), pas quand le modèle « pense » avoir fini ;
- les pannes de modèle basculent sur le suivant, puis réessaient avec patience ;
- l'agent piétine → on passe au modèle le plus fort (si configuré) ;
- l'auto-amélioration peut être confiée entière à Claude (Agent SDK) : voir run_with_claude ;
- le contexte est élagué/résumé pour les longues tâches ;
- chaque étape est persistée : un redémarrage reprend là où on en était.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from dataclasses import replace

from ..auth import tv
from ..config import settings
from ..db import db, now
from ..llm import LLMError, claude_agent, parse_json_loose, registry
from ..llm.base import LLMResponse, estimate_tokens, message_text
from ..tools import TOOLS, ToolContext, execute, tools_for
from .prompts import STABLE, dynamic_block, today_text
from .runner import ConvState, Runner, public_message, save_message

log = logging.getLogger("ely.agent")

TRIVIAL = re.compile(r"^\W*(salut|bonjour|bonsoir|coucou|hello|hi|hey|merci|thanks|ok|okay|d'accord|dac|super|parfait|top|génial|cool|bravo|bonne nuit|à plus)\b[^?]{0,30}$", re.I)
LOST = "[Résultat perdu (Ely a redémarré pendant l'action) : vérifie si elle a eu lieu avant de la refaire.]"
RETRY_DELAYS = [20, 60, 120, 300, 600]

# Modèle demandé dans le message : « prends / utilise le modèle fort » (rôle d'escalade), « utilise Opus / Fable »
# (Claude). Un verbe d'ordre est exigé : « le modèle fort ne répond pas ? » ne demande rien.
_VERB = r"\b(?:prends|prenez|utilise|utilisez|passe|passez|bascule|basculez|use|switch to|with|avec)\b(?:\s+(?:sur|au|vers|a|to))?"
ASKED_MODEL = [
    ("opus", re.compile(_VERB + r"\s+(?:claude\s+)?opus\b")),
    ("fable", re.compile(_VERB + r"\s+(?:claude\s+)?fable\b")),
    ("strong", re.compile(_VERB + r"\s+(?:le|un|ton|votre|the|a|your)?\s*(?:modele|model)\s+(?:le\s+plus\s+)?"
                          r"(?:fort|puissant)\b|" + _VERB + r"\s+(?:the|a|your)?\s*(?:strong(?:est)?|powerful)\s+model\b")),
]
CLAUDE_NAMES = {"opus": "Opus", "fable": "Fable"}
# Outils natifs de Claude affichés pendant une mission (les outils d'Ely gardent leur libellé)
CLAUDE_LABELS = {"Read": ("Lecture du code", "📖"), "Glob": ("Recherche de fichiers", "🔎"),
                 "Grep": ("Recherche dans le code", "🔎"), "Edit": ("Modification du code", "✏️"),
                 "Write": ("Écriture de fichier", "✏️")}


def asked_model(text: str) -> str | None:
    """« strong », « opus » ou « fable » si le message demande ce modèle, sinon None."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()
    return next((name for name, pat in ASKED_MODEL if pat.search(plain)), None)


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
    for i, m in enumerate(msgs):  # les images anciennes coûtent cher : on ne garde que les récentes
        drop_images(m, recent=i >= len(msgs) - 6)
    return normalize(msgs, pending_ids)


def drop_images(m: dict, recent: bool = False) -> bool:
    """Retire les images d'un message, renvoyées par un outil ou jointes par l'utilisateur (photo, capture…) :
    chacune est réencodée à chaque appel du modèle. Une mention les remplace. Message récent : seules partent les
    images dont le contenu a été purgé de la base (vignette sans données)."""
    n = 0 if recent else len(m.pop("images", None) or [])
    if isinstance(m.get("content"), list):
        parts = [p for p in m["content"] if p.get("type") != "image" or (recent and p.get("data"))]
        n += len(m["content"]) - len(parts)
        m["content"] = parts + ([{"type": "text", "text": "[image omise]"}] if n else [])
    elif n:
        m["content"] = f"{m.get('content') or ''} [image omise]".strip()
    return bool(n)


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


async def verify(objective: str, history: list[dict], answer: str, user_id: int, today: str = "") -> tuple[bool, str]:
    when = f"\nNous sommes {today} : juge les dates par rapport à ce jour, pas d'après tes connaissances.\n" if today else ""
    prompt = f"""Tu es le contrôleur qualité d'un agent autonome. Détermine si la DEMANDE de l'utilisateur est entièrement satisfaite.
{when}
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
        changed = drop_images(m) or changed
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
        # session d'auto-amélioration : son propre modèle (Réglages → Modèles → Auto-amélioration)
        self.selfdev = self.conv.get("channel") == "selfdev" and user.get("role") == "admin"
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

    # ---------------------------------------------------------------- choix du modèle
    async def escalate(self, reason: str) -> None:
        """Passe au modèle d'escalade jusqu'à la fin de la tâche, en le disant."""
        strong = registry.resolve("strong")
        if self.escalated or self.selfdev:
            return
        if not strong:
            await self.emit("model", {"model": registry.resolve("main"),
                                      "reason": "aucun modèle d'escalade n'est choisi (Réglages → Modèles) : je continue avec le modèle principal"})
            return
        self.escalated = True
        self.save_state(escalated=True)
        await self.emit("model", {"model": strong, "reason": f"{reason} : bascule sur {strong}"})

    async def honor_request(self, text: str) -> None:
        """« Prends le modèle fort », « utilise Opus »… dans un message de l'utilisateur."""
        asked = asked_model(text)
        if asked == "strong":
            await self.escalate("modèle fort demandé")
        elif asked in CLAUDE_NAMES:  # Claude par l'Agent SDK n'est pas encore relié : le modèle fort le remplace
            await self.escalate(f"Claude {CLAUDE_NAMES[asked]} n'est pas encore relié à Ely, modèle fort à la place")

    # ---------------------------------------------------------------- appel modèle
    async def call_model(self, system: list[str], history: list[dict], schemas: list[dict] | None, model: str | None):
        async def on_delta(kind: str, text: str) -> None:
            await self.emit("delta", {"kind": kind, "text": text})

        async def on_switch(ref: str, reason: str) -> None:
            await self.emit("stream_reset", {})
            await self.emit("model", {"model": ref, "reason": reason})

        if self.selfdev and claude_agent.is_claude(registry.resolve("selfdev")):
            role = "strong" if registry.resolve("strong") else "main"  # Claude a passé la main (panne, reprise)
        elif self.selfdev:
            role = "selfdev"
        elif self.escalated and registry.resolve("strong"):
            role = "strong"
        else:
            role = "main"
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                self.st.partial = ""
                self.st.thinking = ""
                resp = await registry.chat(role=role, model=model if role == "main" else None, system=system,
                                           messages=[{k: v for k, v in m.items() if not k.startswith("_")} for m in history],
                                           tools=schemas, on_delta=on_delta, user_id=self.user["id"], purpose="agent",
                                           on_switch=on_switch)
                await self.emit("model", {"model": resp.model})
                return resp
            except LLMError as e:
                if e.kind == "context" and attempt < 3:
                    await self.compact(history, force=True, model=e.model or None)
                    continue
                if attempt >= len(RETRY_DELAYS) or not e.retryable:  # clé refusée, aucun modèle… : inutile d'attendre
                    raise
                delay = RETRY_DELAYS[attempt]
                await self.emit("status", {"status": "retrying", "detail": f"Modèles indisponibles, nouvel essai dans {delay} s : {str(e)[:200]}"})
                await asyncio.sleep(delay)
        raise LLMError("Le modèle n'a pas pu répondre malgré plusieurs essais.")

    async def compact(self, history: list[dict], force: bool = False, model: str | None = None) -> None:
        """Condense l'historique ; `model` : celui dont le contexte a débordé (sinon le modèle de la conversation)."""
        try:
            ref = [model] if model else registry.chain("main", self.conv.get("model") or None)[:1]
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
            if not isinstance(summary, str) or not summary.strip():
                # Un fournisseur peut répondre sans texte : ce n'est pas un
                # résumé et ne doit jamais faire avancer summary_upto.
                raise ValueError("résumé vide : historique conservé")
        except Exception as e:
            log.warning("résumé impossible : %s", e)
            return
        upto = max((m.get("_id") or 0) for m in history[:cut])
        db.update("conversations", "id = ?", (self.conv_id,), summary=summary, summary_upto=upto)
        history[:cut] = [{"role": "user", "content": f"[Résumé de la conversation jusqu'ici]\n{summary}", "_id": upto}]

    # ---------------------------------------------------------------- exécution
    async def run(self, resume: bool = False) -> None:
        if self.selfdev and await self.delegate_to_claude(resume):
            asyncio.create_task(self.after_run())
            return
        user = self.user
        objective = self.run_row["objective"]
        pending = self.state.get("pending_ask") if resume else None
        history = load_history(self.conv_id, {pending["tool_call_id"]} if pending else set())

        selfdev = self.selfdev
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
        await self.honor_request(objective)

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
                    if q.get("role") == "user" and isinstance(q.get("content"), str):
                        await self.honor_request(q["content"])
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
                done, missing = await verify(objective, run_messages(self.run_id), answer, user["id"], today_text(user))
                await self.emit("verify", {"done": done, "missing": missing})
                if not done:
                    rejections += 1
                    no_progress = no_progress + 1 if successes == 0 else 1
                    successes = 0
                    self.save_state(rejections=rejections, no_progress=no_progress)
                    if rejections < settings.max_verify_retries and no_progress < 3:
                        if registry.resolve("strong"):  # dès le premier échec, sans attendre de piétiner
                            await self.escalate("objectif pas encore atteint, escalade vers le modèle le plus fort")
                        note = {"role": "user", "kind": "control",
                                "content": f"[Contrôle automatique] L'objectif n'est pas encore atteint : {missing}\n"
                                           "Continue sans t'arrêter : agis avec tes outils, change de stratégie si besoin."}
                        history.append(note)
                        await self.publish_message(self.persist(note))
                        continue
            db.run("UPDATE runs SET status = 'done', updated_at = ? WHERE id = ?", (now(), self.run_id))
            break

        asyncio.create_task(self.after_run())

    # ---------------------------------------------------------------- auto-amélioration confiée à Claude
    async def delegate_to_claude(self, resume: bool) -> bool:
        """True si la tâche a été traitée ici : mission Claude menée, ou interrompue par un redémarrage."""
        if resume and self.state.get("claude"):
            # Claude travaillait quand Ely s'est arrêtée : on ne sait pas où il en était, on ne relance pas (LOST)
            note = {"role": "assistant", "kind": "note", "content": (
                "⚠️ La mission confiée à Claude a été interrompue par un redémarrage d'Ely. Elle n'est pas relancée "
                "automatiquement : ses modifications non déployées restent dans la copie de travail. Relancez "
                "l'auto-amélioration pour la reprendre.")}
            await self.publish_message(self.persist(note))
            db.run("UPDATE runs SET status = 'stopped', updated_at = ? WHERE id = ?", (now(), self.run_id))
            return True
        ref = registry.resolve("selfdev")
        if resume or self.state.get("without_claude") or not claude_agent.is_claude(ref):
            return False
        return await self.run_with_claude(ref, self.run_row["objective"])

    async def run_with_claude(self, ref: str, objective: str) -> bool:
        """Mission entière menée par Claude dans la copie de travail. Redémarrage éventuel à la fin seulement.
        False : Claude n'a rien pu faire, la boucle habituelle prend le relais sur l'escalade ou le principal."""
        from ..selfdev import pipeline
        from ..selfdev.tools import claude_session

        ctx = ToolContext(user=self.user, conversation_id=self.conv_id, run_id=self.run_id, emit=self.emit,
                          extra={"selfdev": True, "defer_restart": True})
        acted = False
        result: dict = {}
        actions: list[dict] = []  # pour le compte rendu si la mission doit être relancée sans Claude
        texts: list[str] = []
        await self.emit("status", {"status": "running"})
        await self.emit("model", {"model": ref})
        self.save_state(claude=ref)
        try:
            await pipeline.ensure_session()
            async for ev in claude_agent.run(claude_session(ctx, objective, ref)):
                if ev["type"] == "text":
                    texts.append(ev["text"])
                    await self.emit("stream_reset", {})
                    await self.emit("delta", {"kind": "text", "text": ev["text"]})
                elif ev["type"] == "tool_start":
                    acted = True
                    name, label, icon = _claude_tool(ev["name"])
                    args = {k: v.replace(f"{pipeline.WORKTREE}/", "") if isinstance(v, str) else v
                            for k, v in (ev.get("input") or {}).items()}
                    await self.emit("tool_start", {"id": ev["id"], "name": name, "label": label, "icon": icon,
                                                   "args": _preview_args(args)})
                    actions.append({"id": ev["id"], "label": label, "icon": icon, "args": _preview_args(args)})
                elif ev["type"] == "tool_end":
                    for a in actions:
                        if a["id"] == ev["id"]:
                            a.update(ok=ev["ok"], preview=ev.get("content") or "")
                    await self.emit("tool_end", {"id": ev["id"], "name": _claude_tool(ev["name"])[0], "ok": ev["ok"],
                                                 "preview": (ev.get("content") or "")[:300]})
                elif ev["type"] == "result":
                    result = ev
        except Exception as e:  # SDK absent, CLI introuvable, identifiants refusés, coupure…
            log.warning("mission Claude : %s", e)
            result = {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
        if result.get("input_tokens") or result.get("output_tokens"):
            registry.record_usage(self.user["id"], LLMResponse(
                text="", model=ref, input_tokens=result.get("input_tokens", 0), output_tokens=result.get("output_tokens", 0),
                cached_tokens=result.get("cached_tokens", 0)), "selfdev")
        error = result.get("error") or ("" if result.get("ok") else "réponse incomplète")
        deployed = bool(ctx.extra.get("deployed"))
        if not result.get("ok") and not acted and not deployed:
            fallback = registry.resolve("strong") or registry.resolve("main")
            self.save_state(claude="")
            await self.emit("stream_reset", {})
            await self.emit("model", {"model": fallback or "", "reason": f"{ref} indisponible ({error[:160]}), bascule sur {fallback or '—'}"})
            return False
        if result.get("limit"):  # quota atteint en pleine mission : on arrête, on consigne, on recommence sans Claude
            await self.relaunch_without_claude(ref, objective, error, actions, texts, deployed)
            return True
        if result.get("ok"):
            msg = {"role": "assistant", "content": result.get("text") or "Mission terminée.", "model": ref}
        else:  # Claude a déjà agi : pas de reprise automatique par un autre modèle
            msg = {"role": "assistant", "kind": "note", "model": ref, "content": (
                f"⚠️ La mission confiée à Claude s'est arrêtée : {error}.\n"
                + ("Ce qui a été déployé s'active au redémarrage d'Ely. " if deployed else "")
                + "Les modifications non déployées restent dans la copie de travail ; relancez l'auto-amélioration pour reprendre.")}
        await self.publish_message(self.persist(msg))
        db.run("UPDATE runs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
               ("done" if result.get("ok") else "error", None if result.get("ok") else error[:1000], now(), self.run_id))
        if deployed and os.environ.get("ELY_SUPERVISED") == "1":  # la nouvelle version s'active une fois la mission close
            pipeline.restart_soon()
        return True

    async def relaunch_without_claude(self, ref: str, objective: str, error: str, actions: list[dict], texts: list[str],
                                      deployed: bool) -> None:
        """Claude a agi puis son quota est tombé : son travail est consigné dans un fichier markdown, la tâche s'arrête,
        et la mission recommence sur le modèle d'escalade (sinon le principal) avec ce contexte. Rien n'est repris au
        vol : le nouveau modèle repart de la mission, en sachant ce qui est déjà en place."""
        from ..selfdev import pipeline
        from ..selfdev.tools import claude_report, relaunch_objective

        try:
            diff = await pipeline.diff()
        except Exception as e:
            diff = f"(état de la copie de travail illisible : {e})"
        report = claude_report(self.user, objective, ref, error, actions, texts, diff, deployed)
        fallback = registry.resolve("strong") or registry.resolve("main") or "—"
        note = {"role": "assistant", "kind": "note", "model": ref, "content": (
            f"⚠️ Quota de Claude atteint en pleine mission ({error[:200]}). Ce qu'il a fait est consigné dans Fichiers → "
            f"{report}. La mission recommence avec {fallback}, qui en tient compte.")}
        await self.publish_message(self.persist(note))
        db.run("UPDATE runs SET status = 'stopped', error = ?, updated_at = ? WHERE id = ?", (error[:1000], now(), self.run_id))
        await self.emit("model", {"model": fallback, "reason": f"quota de Claude atteint : mission relancée avec {fallback}"})
        text = relaunch_objective(objective, report, actions, diff, deployed)
        task = self.st.task

        async def relaunch() -> None:  # une fois la tâche de Claude close
            if task and task is not asyncio.current_task():
                await asyncio.wait({task})
            if not self.runner.shutting_down:
                await self.runner.submit(self.user, self.conv_id, text, channel="selfdev", kind="relaunch",
                                         state={"without_claude": True})

        asyncio.get_running_loop().create_task(relaunch())
        if deployed and os.environ.get("ELY_SUPERVISED") == "1":
            pipeline.restart_soon()

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
            await notify(self.user["id"], "Ely · tâche planifiée", text, url=f"/?c={self.conv_id}")  # coupé pour le push seulement
        try:
            await learn_from_run(self.user, self.conv_id, self.run_id)
        except Exception as e:
            log.info("apprentissage : %s", e)


def _claude_tool(name: str) -> tuple[str, str, str]:
    """Nom, libellé et icône d'un outil utilisé par Claude."""
    short = name.removeprefix(f"mcp__{claude_agent.BRIDGE}__")
    if short != name and short in TOOLS:
        return short, TOOLS[short].label, TOOLS[short].icon
    label, icon = CLAUDE_LABELS.get(name, (name, "⚙️"))
    return name, label, icon


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
