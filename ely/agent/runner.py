"""Gestionnaire des tâches : chaque demande devient une tâche de fond persistante.

- indépendante de la connexion (on peut fermer l'appli, la tâche continue)
- reprise automatique après un redémarrage du serveur
- messages ajoutés en cours de route intégrés à la tâche en cours
- flux d'événements temps réel par utilisateur (SSE), avec état instantané à la connexion
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from ..auth import get_user
from ..db import db, now
from ..llm.base import message_text
from ..memory.store import index_message

log = logging.getLogger("ely.runner")


@dataclass
class ConvState:
    conversation_id: int
    user_id: int
    run_id: int | None = None
    task: asyncio.Task | None = None
    status: str = "idle"  # idle | running | waiting_user
    partial: str = ""
    thinking: str = ""
    activity: str = ""
    model: str = ""
    queue: list[dict] = field(default_factory=list)
    ask: dict | None = None
    ask_future: asyncio.Future | None = None
    last_frame: dict | None = None
    live_tools: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {"conversation_id": self.conversation_id, "run_id": self.run_id, "status": self.status,
                "partial": self.partial, "thinking": self.thinking[-2000:], "activity": self.activity, "model": self.model,
                "ask": self.ask, "has_frame": bool(self.last_frame), "live_tools": list(self.live_tools.values())}


def save_message(conversation_id: int, run_id: int | None, msg: dict, user_id: int | None = None) -> int:
    mid = db.insert("messages", conversation_id=conversation_id, run_id=run_id, role=msg["role"],
                    data=json.dumps(msg, ensure_ascii=False), visible=1, created_at=now())
    db.run("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), conversation_id))
    if msg["role"] in ("user", "assistant") and user_id and msg.get("kind") != "control":
        index_message(mid, conversation_id, user_id, message_text(msg))
    return mid


def public_message(row: dict) -> dict:
    """Message tel qu'envoyé à l'interface (sans images base64 volumineuses)."""
    data = json.loads(row["data"]) if isinstance(row.get("data"), str) else row["data"]
    data = dict(data)
    data.pop("thinking", None)
    if data.get("images"):
        data["images"] = len(data["images"])
    if data.get("role") == "tool" and len(data.get("content") or "") > 4000:
        data["content"] = data["content"][:4000] + "…"
    c = data.get("content")
    if isinstance(c, list):
        data["content"] = [p if p.get("type") != "image" else {"type": "image", "media_type": p.get("media_type"),
                                                                "src": p.get("src", "")} for p in c]
    return {"id": row["id"], "run_id": row.get("run_id"), "created_at": row["created_at"], **data}


class Runner:
    def __init__(self) -> None:
        self.states: dict[int, ConvState] = {}
        self.subscribers: dict[int, set[asyncio.Queue]] = {}
        self.shutting_down = False
        self.listeners: list = []  # canaux externes (Telegram…) : async fn(user_id, event)

    # ------------------------------------------------------------------ événements
    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.setdefault(user_id, set()).add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        self.subscribers.get(user_id, set()).discard(q)

    def publish(self, user_id: int, event: dict) -> None:
        for q in list(self.subscribers.get(user_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # client lent : on jette le plus ancien, jamais les fins de tâche
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        for fn in self.listeners:
            if event["type"] in ("message", "ask_user", "tool_start", "run_end"):
                asyncio.ensure_future(fn(user_id, event))

    def state(self, conversation_id: int, user_id: int) -> ConvState:
        st = self.states.get(conversation_id)
        if not st:
            st = self.states[conversation_id] = ConvState(conversation_id, user_id)
        return st

    def snapshots(self, user_id: int) -> list[dict]:
        return [s.snapshot() for s in self.states.values() if s.user_id == user_id and (s.status != "idle" or s.last_frame)]

    async def emit(self, st: ConvState, type_: str, data: dict | None = None) -> None:
        data = data or {}
        if type_ == "delta":
            if data.get("kind") == "thinking":
                st.thinking += data["text"]
            else:
                st.partial += data["text"]
        elif type_ == "stream_reset":
            st.partial = ""
        elif type_ == "browser_frame":
            st.last_frame = data
        elif type_ == "tool_start":
            st.live_tools[data["id"]] = data
            st.activity = data.get("label", "")
        elif type_ == "tool_end":
            st.live_tools.pop(data["id"], None)
        elif type_ == "model":
            st.model = data.get("model", "")
        self.publish(st.user_id, {"type": type_, "conversation_id": st.conversation_id, "run_id": st.run_id, **data})

    # ------------------------------------------------------------------ soumission
    async def submit(self, user: dict, conversation_id: int, content, *, channel: str = "web", kind: str = "",
                     state: dict | None = None) -> dict:
        """Ajoute un message de l'utilisateur ; démarre une tâche ou l'intègre à celle en cours."""
        msg = {"role": "user", "content": content}
        if kind:
            msg["kind"] = kind
        st = self.state(conversation_id, user["id"])
        mid = save_message(conversation_id, st.run_id if st.task else None, msg, user["id"])
        row = db.one("SELECT * FROM messages WHERE id = ?", (mid,))
        await self.emit(st, "message", {"message": public_message(row)})
        text = message_text(msg)
        if st.ask_future and not st.ask_future.done() and not kind:
            st.ask_future.set_result(text or "(réponse vide)")
            return {"message_id": mid, "run_id": st.run_id, "mode": "answer"}
        if st.task and not st.task.done():
            st.queue.append({**msg, "_id": mid})
            db.run("UPDATE runs SET objective = objective || ? WHERE id = ?", (f"\n+ {text}", st.run_id))
            await self.emit(st, "queued", {"message_id": mid})
            return {"message_id": mid, "run_id": st.run_id, "mode": "queued"}
        run_id = db.insert("runs", conversation_id=conversation_id, user_id=user["id"], status="running", objective=text or "(pièce jointe)",
                           state=json.dumps({"channel": channel, "start_message": mid, **(state or {})}),
                           created_at=now(), updated_at=now())
        db.run("UPDATE messages SET run_id = ? WHERE id = ?", (run_id, mid))
        self._start(st, run_id, user, resume=False)
        return {"message_id": mid, "run_id": run_id, "mode": "started"}

    def _start(self, st: ConvState, run_id: int, user: dict, resume: bool) -> None:
        from .loop import AgentLoop

        st.run_id = run_id
        st.status = "running"
        st.partial = st.thinking = st.activity = ""
        st.live_tools.clear()

        async def body():
            loop = AgentLoop(self, st, run_id, user)
            try:
                await loop.run(resume=resume)
            except asyncio.CancelledError:
                if self.shutting_down:  # redémarrage : la tâche reste « running » et sera reprise
                    raise
                db.run("UPDATE runs SET status = 'cancelled', updated_at = ? WHERE id = ?", (now(), run_id))
                note = {"role": "assistant", "content": "⏹️ Tâche arrêtée.", "kind": "note"}
                mid = save_message(st.conversation_id, run_id, note)
                await self.emit(st, "message", {"message": public_message(db.one("SELECT * FROM messages WHERE id = ?", (mid,)))})
            except Exception as e:
                log.exception("tâche %s", run_id)
                db.run("UPDATE runs SET status = 'error', error = ?, updated_at = ? WHERE id = ?", (str(e)[:1000], now(), run_id))
                from ..llm import LLMError

                hint = (" — vérifie les clés d'API dans .env ou que LM Studio est lancé (Réglages → Modèles)."
                        if isinstance(e, LLMError) and "Aucun modèle disponible" not in str(e) else "")
                note = {"role": "assistant", "content": f"⚠️ {e}{hint}", "kind": "note"}
                mid = save_message(st.conversation_id, run_id, note)
                await self.emit(st, "message", {"message": public_message(db.one("SELECT * FROM messages WHERE id = ?", (mid,)))})
            finally:
                if self.shutting_down:
                    return
                st.status = "idle"
                st.ask = None
                st.ask_future = None
                st.partial = st.activity = ""
                st.live_tools.clear()
                status = db.val("SELECT status FROM runs WHERE id = ?", (run_id,))
                await self.emit(st, "run_end", {"status": status})
                if st.queue:  # messages arrivés trop tard pour la tâche : nouvelle tâche
                    pending = st.queue[:]
                    st.queue.clear()
                    text = "\n".join(message_text(m) for m in pending)
                    new_id = db.insert("runs", conversation_id=st.conversation_id, user_id=user["id"], status="running",
                                       objective=text, state=json.dumps({"start_message": pending[0]["_id"]}),
                                       created_at=now(), updated_at=now())
                    db.run(f"UPDATE messages SET run_id = ? WHERE id IN ({','.join('?' * len(pending))})",
                           [new_id, *[m["_id"] for m in pending]])
                    self._start(st, new_id, user, resume=False)

        st.task = asyncio.create_task(body())

    async def cancel(self, conversation_id: int) -> bool:
        st = self.states.get(conversation_id)
        if st and st.task and not st.task.done():
            st.queue.clear()
            st.task.cancel()
            return True
        return False

    async def resume_all(self) -> int:
        """Au démarrage : reprend toutes les tâches interrompues."""
        rows = db.all("SELECT id, conversation_id, user_id FROM runs WHERE status IN ('running', 'waiting_user') ORDER BY id")
        n = 0
        for r in rows:
            user = get_user(r["user_id"])
            if not user:
                continue
            st = self.state(r["conversation_id"], r["user_id"])
            if st.task and not st.task.done():
                db.run("UPDATE runs SET status = 'superseded' WHERE id = ?", (r["id"],))
                continue
            self._start(st, r["id"], user, resume=True)
            n += 1
        return n

    async def shutdown(self) -> None:
        self.shutting_down = True
        tasks = [s.task for s in self.states.values() if s.task and not s.task.done()]
        for t in tasks:
            t.cancel()
        # les tâches annulées au redémarrage restent « running » pour être reprises
        await asyncio.gather(*tasks, return_exceptions=True)


runner = Runner()
