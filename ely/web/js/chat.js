// Vue conversation : fil, étapes d'action, question de l'agent, saisie, navigateur en direct.
import { html, useEffect, useRef, useState, useMemo, useCallback } from "/static/vendor/preact-htm.js";
import { api, get, post, upload } from "/static/js/api.js";
import { Icon, md, fileIcon, isImage, isMobile, toast } from "/static/js/util.js";
import { listen, voiceSupported, stopSpeaking } from "/static/js/voice.js";

export const SUGGESTIONS = [
  ["📅 Prendre un rendez-vous", "Prends-moi un rendez-vous chez un médecin généraliste près de chez moi cette semaine, en fin de journée."],
  ["✍️ Publier sur LinkedIn", "Rédige et publie sur LinkedIn un post engageant sur "],
  ["✉️ Écrire un e-mail", "Écris un e-mail à "],
  ["🗓️ Ma semaine", "Qu'est-ce que j'ai dans mon agenda cette semaine ? Signale-moi les conflits."],
  ["🔎 Rechercher", "Fais une recherche approfondie et un résumé clair sur "],
  ["⏰ Me rappeler", "Rappelle-moi demain à 9h de "],
];

function contentText(m) {
  const c = m.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.filter((p) => p.type === "text").map((p) => p.text).join("\n");
  return "";
}

// Regroupe les messages en éléments affichables (texte, étapes, notes…)
function buildItems(messages) {
  const results = {};
  for (const m of messages) if (m.role === "tool") results[m.tool_call_id] = m;
  const items = [];
  for (const m of messages) {
    if (m.role === "tool") continue;
    if (m.role === "user") {
      if (m.kind === "control") items.push({ type: "control", msg: m });
      else if (m.kind === "scheduled") items.push({ type: "scheduled", msg: m });
      else items.push({ type: "user", msg: m });
      continue;
    }
    if (m.kind === "note") { items.push({ type: "note", msg: m }); continue; }
    if (m.content) items.push({ type: "text", msg: m });
    if (m.tool_calls?.length) {
      const calls = m.tool_calls.map((c) => ({ call: c, result: results[c.id] }));
      const last = items[items.length - 1];
      if (last && last.type === "steps" && last.run_id === m.run_id && !m.content) last.calls.push(...calls);
      else items.push({ type: "steps", calls, run_id: m.run_id, id: m.id });
    }
  }
  return items;
}

function argsPreview(args) {
  if (!args) return "";
  const keys = ["query", "url", "action", "to", "subject", "title", "name", "platform", "path", "instruction", "question", "fact", "text", "location", "command"];
  const parts = [];
  for (const k of keys) if (args[k]) parts.push(String(args[k]).slice(0, 90));
  if (!parts.length) {
    const first = Object.values(args)[0];
    if (first) parts.push(String(typeof first === "string" ? first : JSON.stringify(first)).slice(0, 90));
  }
  return parts.join(" · ");
}

function Steps({ item, tools, running, onImage }) {
  const done = item.calls.filter((c) => c.result).length;
  const errors = item.calls.filter((c) => c.result?.is_error).length;
  const pending = item.calls.length - done;
  const [open, setOpen] = useState(null);
  const [expanded, setExpanded] = useState({});
  const isOpen = open ?? (running && pending > 0);
  const files = item.calls.flatMap((c) => c.result?.files || []);
  const icons = [...new Set(item.calls.map((c) => tools[c.call.name]?.icon || "⚙️"))].slice(0, 6).join(" ");
  const visible = isOpen ? item.calls : [];
  return html`
    <div class=${"steps" + (isOpen ? " open" : "")}>
      <div class="steps-head" onClick=${() => setOpen(!isOpen)}>
        ${pending > 0 && running ? html`<div class="spinner"></div>` : html`<span class=${errors && !done ? "cross" : "check"}>${errors === item.calls.length ? "✗" : "✓"}</span>`}
        <span>${pending > 0 && running ? `En cours · ${done}/${item.calls.length} action${item.calls.length > 1 ? "s" : ""}` :
          `${item.calls.length} action${item.calls.length > 1 ? "s" : ""}${errors ? ` · ${errors} à revoir` : ""}`}</span>
        <span class="faint">${icons}</span>
        <span class="chev"><${Icon} name="chev" size=${16} /></span>
      </div>
      ${visible.map(({ call, result }) => {
        const meta = tools[call.name] || {};
        const st = result ? (result.is_error ? "error" : "ok") : (running ? "run" : "lost");
        return html`
          <div class=${"step" + (st === "error" ? " error" : "")} key=${call.id}>
            <span class="ic">${meta.icon || "⚙️"}</span>
            <div class="what" onClick=${() => setExpanded({ ...expanded, [call.id]: !expanded[call.id] })} style="cursor:pointer">
              <b>${meta.label || call.name}</b>
              <div class="args">${argsPreview(call.arguments)}</div>
              ${expanded[call.id] && result ? html`<div class="res">${(result.content || "").slice(0, 4000)}</div>` : null}
            </div>
            <span class="st">${st === "run" ? html`<div class="spinner"></div>` : st === "ok" ? html`<span class="check">✓</span>` :
              st === "error" ? html`<span class="cross">✗</span>` : html`<span class="faint">–</span>`}</span>
          </div>`;
      })}
    </div>
    ${files.length ? html`<div class="files">${files.map((f) => isImage(f)
      ? html`<img class="shot" src=${"/files/" + encodeURI(f)} style="max-width:260px" onClick=${() => onImage("/files/" + encodeURI(f))} />`
      : html`<a class="file-chip" href=${"/files/" + encodeURI(f) + "?download=1"} target="_blank">${fileIcon(f)} ${f.split("/").pop()}</a>`)}</div>` : null}`;
}

function UserMsg({ msg, onImage }) {
  const images = Array.isArray(msg.content) ? msg.content.filter((p) => p.type === "image" && p.src) : [];
  const text = contentText(msg).replace(/\n*\[Fichier joint : [^\]]+\]/g, "");
  const files = [...contentText(msg).matchAll(/\[Fichier joint : ([^\]]+)\]/g)].map((m) => m[1]).filter((f) => !isImage(f));
  return html`<div class="msg user"><div class="bubble">${text}
    ${images.map((p) => html`<img src=${p.src} onClick=${() => onImage(p.src)} />`)}
    ${files.length ? html`<div class="files" style="margin-top:8px">${files.map((f) => html`<a class="file-chip" href=${"/files/" + encodeURI(f)} target="_blank">${fileIcon(f)} ${f.split("/").pop()}</a>`)}</div>` : null}
  </div></div>`;
}

function Markdown({ text, streaming }) {
  return html`<div class=${"md" + (streaming ? " cursor" : "")} dangerouslySetInnerHTML=${{ __html: md(text) }}></div>`;
}

function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const s = Math.max(0, Math.floor((Date.now() - since) / 1000));
  return html`<span class="faint">${s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60 ? (s % 60) + " s" : ""}`}</span>`;
}

export function Thread({ messages, live, tools, onSend, onImage, onCancel }) {
  const items = useMemo(() => buildItems(messages || []), [messages]);
  const running = live && live.status && live.status !== "idle";
  const sinceRef = useRef(Date.now());
  useEffect(() => { if (running) sinceRef.current = live.since || Date.now(); }, [running]);
  const lastRun = items.length ? items[items.length - 1] : null;
  const Mark = ({ i }) => (i > 0 && ["text", "steps"].includes(items[i - 1].type) ? html`<div class="logo-mark ghost"></div>` : html`<div class="logo-mark"></div>`);
  return html`<div class="thread">
    ${items.map((it, i) => {
      if (it.type === "user") return html`<${UserMsg} key=${it.msg.id} msg=${it.msg} onImage=${onImage} />`;
      if (it.type === "control") return html`<div class="note control" key=${it.msg.id}><span class="i">🔁</span><span>${contentText(it.msg).replace(/^\[Contrôle automatique\]\s*/, "").split("\n")[0]}</span></div>`;
      if (it.type === "scheduled") return html`<div class="note" key=${it.msg.id}><span class="i">⏰</span><span>${contentText(it.msg).replace(/^\[Tâche planifiée #\d+\]\s*/, "Tâche planifiée : ")}</span></div>`;
      if (it.type === "note") return html`<div class="note" key=${it.msg.id}><span>${contentText(it.msg)}</span></div>`;
      if (it.type === "steps") return html`<div class="msg assistant tight" key=${"s" + it.id}><${Mark} i=${i} /><div class="body">
        <${Steps} item=${it} tools=${tools} running=${running && i >= items.length - 2} onImage=${onImage} /></div></div>`;
      return html`<div class="msg assistant" key=${it.msg.id}><${Mark} i=${i} /><div class="body"><${Markdown} text=${it.msg.content} /></div></div>`;
    })}
    ${running && live.thinking && !live.partial ? html`<div class="msg assistant"><div class="logo-mark"></div><div class="body"><div class="thinking">${live.thinking.slice(-600)}</div></div></div>` : null}
    ${running && live.partial ? html`<div class="msg assistant"><div class="logo-mark"></div><div class="body"><${Markdown} text=${live.partial} streaming /></div></div>` : null}
    ${live?.ask ? html`<div class="ask-card">
        <div class="q">❓ ${live.ask.question}</div>
        ${live.ask.options?.length ? html`<div class="opts">${live.ask.options.map((o) => html`<button class="btn small" onClick=${() => onSend(o)}>${o}</button>`)}</div>` : null}
        <div class="small muted">Réponds ci-dessous, Ely reprendra aussitôt.</div>
      </div>` : null}
    ${running && !live.ask && !live.partial ? html`<div class="working">
        <span class="dots"><i></i><i></i><i></i></span>
        <span>${live.detail || (live.activity ? `Ely travaille · ${live.activity}` : "Ely réfléchit")}</span>
        <${Elapsed} since=${sinceRef.current} />
        <button class="btn small ghost" onClick=${onCancel}>Arrêter</button>
      </div>` : null}
    ${live?.notice ? html`<div class="note"><span class="i">↪</span><span>${live.notice}</span></div>` : null}
  </div>`;
}

export function Composer({ onSend, running, onCancel, prefill, autoVoice, disabled }) {
  const [text, setText] = useState(prefill || "");
  const [atts, setAtts] = useState([]);
  const [busy, setBusy] = useState(0);
  const [focus, setFocus] = useState(false);
  const [drag, setDrag] = useState(false);
  const [listening, setListening] = useState(false);
  const ta = useRef();
  const stopRef = useRef();
  const fileRef = useRef();

  useEffect(() => { if (prefill) { setText(prefill); setTimeout(() => ta.current?.focus(), 50); } }, [prefill]);
  useEffect(() => { autoGrow(); }, [text]);
  useEffect(() => { if (autoVoice) startVoice(); }, [autoVoice]);

  function autoGrow() {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px";
  }

  async function addFiles(files) {
    for (const f of files) {
      setBusy((b) => b + 1);
      try {
        const res = await upload(f);
        setAtts((a) => [...a, { path: res.path, name: f.name }]);
      } catch (e) { toast(`Envoi impossible : ${e.message}`); }
      setBusy((b) => b - 1);
    }
  }

  function send(value) {
    const t = (value ?? text).trim();
    if ((!t && !atts.length) || busy) return;
    onSend(t, atts.map((a) => a.path));
    setText("");
    setAtts([]);
  }

  function startVoice() {
    if (listening) { stopRef.current?.(); return; }
    if (!voiceSupported()) { toast("La dictée n'est pas disponible sur ce navigateur."); return; }
    stopSpeaking();
    setListening(true);
    let last = "";
    stopRef.current = listen({
      onText: (t, final) => { last = t; setText(t); if (final && t) { setListening(false); send(t); } },
      onEnd: () => setListening(false),
      onError: (e) => { setListening(false); toast(e === "not-allowed" ? "Autorise le micro pour dicter (connexion HTTPS requise)." : `Micro : ${e}`); },
    });
  }

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !isMobile() && !e.isComposing) { e.preventDefault(); send(); }
  };
  const onPaste = (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  };

  return html`<div class="composer-wrap">
    <div class=${"composer" + (focus ? " focus" : "") + (drag ? " drag" : "")}
         onDragOver=${(e) => { e.preventDefault(); setDrag(true); }} onDragLeave=${() => setDrag(false)}
         onDrop=${(e) => { e.preventDefault(); setDrag(false); addFiles([...e.dataTransfer.files]); }}>
      ${atts.length || busy ? html`<div class="attachments">
        ${atts.map((a) => html`<span class="att">${fileIcon(a.name)} <span>${a.name}</span><button onClick=${() => setAtts(atts.filter((x) => x !== a))}>✕</button></span>`)}
        ${busy ? html`<span class="att"><div class="spinner"></div><span>Envoi…</span></span>` : null}
      </div>` : null}
      <textarea ref=${ta} rows="1" value=${text} disabled=${disabled}
        placeholder=${listening ? "Je t'écoute…" : running ? "Ajoute une précision, Ely en tiendra compte…" : "Demande n'importe quoi à Ely…"}
        onInput=${(e) => setText(e.target.value)} onKeyDown=${onKey} onPaste=${onPaste}
        onFocus=${() => setFocus(true)} onBlur=${() => setFocus(false)}></textarea>
      <div class="bar">
        <input type="file" multiple ref=${fileRef} style="display:none" onChange=${(e) => { addFiles([...e.target.files]); e.target.value = ""; }} />
        <button class="icon-btn" title="Joindre un fichier ou une photo" onClick=${() => fileRef.current.click()}><${Icon} name="clip" /></button>
        <div class="spacer"></div>
        ${running ? html`<button class="send stop" title="Arrêter la tâche" onClick=${onCancel}><${Icon} name="stop" /></button>` : null}
        ${text.trim() || atts.length ? html`<button class="send" title="Envoyer" onClick=${() => send()} disabled=${busy > 0}><${Icon} name="send" /></button>`
          : html`<button class=${"send mic" + (listening ? " on" : "")} title="Parler à Ely" onClick=${startVoice}><${Icon} name="mic" /></button>`}
      </div>
    </div>
    <div class="hint">Entrée pour envoyer · Maj+Entrée pour aller à la ligne · glisse un fichier pour le joindre</div>
  </div>`;
}

export function LiveBrowser({ frame, conversationId, onClose, mobile }) {
  const [img, setImg] = useState(frame);
  const [control, setControl] = useState(false);
  const [typed, setTyped] = useState("");
  const [goto, setGoto] = useState("");
  useEffect(() => { if (frame) setImg(frame); }, [frame]);
  useEffect(() => { if (!frame) refresh(); }, []);

  async function refresh() {
    try { const r = await get(`/api/browser/frame?fresh=1${conversationId ? "&conversation_id=" + conversationId : ""}`); if (r.image) setImg(r); } catch {}
  }
  async function act(action, extra = {}) {
    try { const r = await post("/api/browser/action", { action, ...extra }); setImg(r); } catch (e) { toast(e.message); }
  }
  function click(e) {
    if (!control) return;
    const rect = e.target.getBoundingClientRect();
    act("click", { x: (e.clientX - rect.left) / rect.width, y: (e.clientY - rect.top) / rect.height });
  }
  return html`<aside class="live">
    <div class="live-head">
      <span>🌐</span>
      <span class="url">${img?.url || "Navigateur d'Ely"}</span>
      <button class="icon-btn" title="Actualiser" onClick=${refresh}><${Icon} name="refresh" size=${18} /></button>
      <button class="icon-btn" title="Fermer" onClick=${onClose}><${Icon} name="close" size=${18} /></button>
    </div>
    <div class=${"live-view" + (control ? " control" : "")}>
      ${img?.image ? html`<img src=${"data:image/jpeg;base64," + img.image} onClick=${click} />`
        : html`<div class="live-empty">Le navigateur d'Ely s'affichera ici dès qu'elle l'utilisera.<br/><br/>
            Tu peux aussi l'ouvrir toi-même pour te connecter une fois à tes sites (Doctolib, LinkedIn…).</div>`}
    </div>
    <div class="live-foot">
      <div class="row">
        <label class="row small" style="gap:8px"><span class="switch"><input type="checkbox" checked=${control} onChange=${(e) => setControl(e.target.checked)} /><span></span></span>
          Prendre la main</label>
        <span class="faint small">${control ? "Touche l'image pour cliquer" : "Pour te connecter, résoudre un captcha…"}</span>
      </div>
      ${control ? html`
        <div class="row" style="flex-wrap:nowrap">
          <input class="input" placeholder="Texte à taper" value=${typed} onInput=${(e) => setTyped(e.target.value)}
                 onKeyDown=${(e) => { if (e.key === "Enter") { act("type", { text: typed }); setTyped(""); } }} />
          <button class="btn small" onClick=${() => { act("type", { text: typed }); setTyped(""); }}>Taper</button>
        </div>
        <div class="row">
          ${[["Entrée", "Enter"], ["Tab", "Tab"], ["⌫", "Backspace"], ["Échap", "Escape"]].map(([l, k]) => html`<button class="btn small" onClick=${() => act("key", { text: k })}>${l}</button>`)}
          <button class="btn small" onClick=${() => act("scroll", { y: -1 })}>↑</button>
          <button class="btn small" onClick=${() => act("scroll", { y: 1 })}>↓</button>
          <button class="btn small" onClick=${() => act("back")}>← Retour</button>
        </div>
        <div class="row" style="flex-wrap:nowrap">
          <input class="input" placeholder="Aller à… (ex. doctolib.fr)" value=${goto} onInput=${(e) => setGoto(e.target.value)}
                 onKeyDown=${(e) => { if (e.key === "Enter" && goto) act("goto", { text: goto }); }} />
          <button class="btn small" onClick=${() => goto && act("goto", { text: goto })}>Ouvrir</button>
        </div>` : null}
    </div>
  </aside>`;
}
