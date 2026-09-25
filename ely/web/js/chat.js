// Vue conversation : fil, actions, question de l'agent, saisie, navigateur en direct.
import { html, useEffect, useRef, useState, useMemo } from "/static/vendor/preact-htm.js";
import { get, post, upload } from "/static/js/api.js";
import { FileTag, Icon, clock, md, isImage, isMobile, toast } from "/static/js/util.js";
import { listen, voiceSupported, stopSpeaking } from "/static/js/voice.js";

export const SUGGESTIONS = [
  ["Prendre un rendez-vous", "Prends-moi un rendez-vous chez un médecin généraliste près de chez moi cette semaine, en fin de journée."],
  ["Publier sur LinkedIn", "Rédige et publie sur LinkedIn un post engageant sur "],
  ["Écrire un e-mail", "Écris un e-mail à "],
  ["Ma semaine", "Qu'est-ce que j'ai dans mon agenda cette semaine ? Signale-moi les conflits."],
  ["Rechercher", "Fais une recherche approfondie et un résumé clair sur "],
  ["Me rappeler", "Rappelle-moi demain à 9h de "],
];

function contentText(m) {
  const c = m.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.filter((p) => p.type === "text").map((p) => p.text).join("\n");
  return "";
}

// Regroupe les messages en éléments affichables (texte, actions, notes…)
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
      else items.push({ type: "steps", calls, run_id: m.run_id, id: m.id, msg: m });
    }
  }
  return items;
}

// Blocs du fil : messages de l'utilisateur, tâches planifiées, et « tours » d'Ely (en-tête « ely · 19:38 »)
function buildBlocks(items) {
  const blocks = [];
  let turn = null;
  for (const it of items) {
    if (it.type === "user" || it.type === "scheduled") { blocks.push(it); turn = null; continue; }
    if (it.type === "control") {
      // le contrôleur d'objectif clôt le tour qu'il juge ; la suite ouvre un nouveau tour
      if (turn) { turn.items.push(it); turn.closed = true; turn = null; } else blocks.push(it);
      continue;
    }
    if (!turn) { turn = { type: "turn", items: [], ts: it.msg.created_at, key: "t" + it.msg.id }; blocks.push(turn); }
    turn.items.push(it);
  }
  return blocks;
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

// « web_search → huggingface.co » : cible courte d'un appel
function target(args) {
  if (!args) return "";
  const url = args.url || (typeof args.text === "string" && /^https?:\/\//.test(args.text) ? args.text : "");
  if (url) { try { return new URL(url.startsWith("http") ? url : "https://" + url).hostname.replace(/^www\./, ""); } catch { /* adresse partielle */ } }
  const v = args.query || args.action || args.to || args.path || args.location || args.name || "";
  const t = String(v).trim();
  return t.length > 42 ? t.slice(0, 41) + "…" : t;
}

function Steps({ item, running, onImage }) {
  const done = item.calls.filter((c) => c.result).length;
  const errors = item.calls.filter((c) => c.result?.is_error).length;
  const pending = item.calls.length - done;
  const [open, setOpen] = useState(null);
  const [expanded, setExpanded] = useState({});
  const busy = running && pending > 0;
  const isOpen = open ?? busy;
  const files = item.calls.flatMap((c) => c.result?.files || []);
  const n = item.calls.length;
  const names = [...new Set(item.calls.map((c) => c.call.name))];
  const last = item.calls[item.calls.length - 1].call;
  const sum = names.length === 1 ? [names[0], target(last.arguments)].filter(Boolean).join(" → ")
    : names.slice(0, 3).join(", ") + (names.length > 3 ? "…" : "");
  return html`
    <div class=${"steps" + (isOpen ? " open" : "")}>
      <button class="steps-head" onClick=${() => setOpen(!isOpen)} aria-expanded=${isOpen}>
        ${busy ? html`<div class="spinner"></div>` : errors === n ? html`<span class="err">✗</span>` : html`<${Icon} name="check" size=${12} stroke=${1.6} />`}
        <span class="cnt">${busy ? `En cours · ${done}/${n}` : `${n} action${n > 1 ? "s" : ""}`}${errors && !busy ? html` <span class="err">· ${errors} à revoir</span>` : null}</span>
        <span class="sum">${sum}</span>
        <span class="chev">${isOpen ? "−" : "+"}</span>
      </button>
      ${isOpen ? html`<div class="steps-list">${item.calls.map(({ call, result }) => {
        const st = result ? (result.is_error ? "error" : "ok") : (running ? "run" : "lost");
        const args = argsPreview(call.arguments);
        return html`
          <div class="step" key=${call.id}>
            <span class=${"st " + st}>${st === "run" ? html`<div class="spinner"></div>` : st === "ok" ? "✓" : st === "error" ? "✗" : "–"}</span>
            <div class="what" onClick=${() => setExpanded({ ...expanded, [call.id]: !expanded[call.id] })}>
              <div class="line"><span class="name">${call.name}</span>${args ? html`<span class="args">  ${args}</span>` : null}</div>
              ${expanded[call.id] && result ? html`<div class="res">${(result.content || "").slice(0, 4000)}</div>` : null}
            </div>
          </div>`;
      })}</div>` : null}
    </div>
    ${files.length ? html`<div class="files">${files.map((f) => isImage(f)
      ? html`<img class="shot" src=${"/files/" + encodeURI(f)} style="max-width:280px" onClick=${() => onImage("/files/" + encodeURI(f))} />`
      : html`<a class="file-chip" href=${"/files/" + encodeURI(f) + "?download=1"} target="_blank"><${FileTag} path=${f} /><span>${f.split("/").pop()}</span></a>`)}</div>` : null}`;
}

function UserMsg({ msg, onImage }) {
  const images = Array.isArray(msg.content) ? msg.content.filter((p) => p.type === "image" && p.src) : [];
  const text = contentText(msg).replace(/\n*\[Fichier joint : [^\]]+\]/g, "");
  const files = [...contentText(msg).matchAll(/\[Fichier joint : ([^\]]+)\]/g)].map((m) => m[1]).filter((f) => !isImage(f));
  return html`<div class="msg user"><div class="bubble">${text}
    ${images.map((p) => html`<img src=${p.src} onClick=${() => onImage(p.src)} />`)}
    ${files.length ? html`<div class="files" style="margin-top:8px">${files.map((f) => html`<a class="file-chip" href=${"/files/" + encodeURI(f)} target="_blank"><${FileTag} path=${f} /><span>${f.split("/").pop()}</span></a>`)}</div>` : null}
  </div></div>`;
}

function Markdown({ text, streaming }) {
  return html`<div class=${"md" + (streaming ? " cursor" : "")} dangerouslySetInnerHTML=${{ __html: md(text) }}></div>`;
}

const Trace = ({ tag, children }) => html`<div class="trace"><span class="tag">${tag}</span><span>${children}</span></div>`;

function controlText(msg) {
  return contentText(msg).replace(/^\[Contrôle automatique\]\s*/, "").replace(/^L'objectif n'est pas encore atteint\s*:\s*/, "").split("\n")[0];
}

function Elapsed({ since }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const s = Math.max(0, Math.floor((Date.now() - since) / 1000));
  return html`<span>${s < 60 ? `${s} s` : `${Math.floor(s / 60)} min${s % 60 ? " " + (s % 60) + " s" : ""}`}</span>`;
}

const Meta = ({ ts }) => html`<div class="turn-meta"><span class="mk"></span><span>ely</span><span>·</span><span>${clock(ts)}</span></div>`;

export function Thread({ messages, live, onSend, onImage, onCancel }) {
  const items = useMemo(() => buildItems(messages || []), [messages]);
  const blocks = useMemo(() => buildBlocks(items), [items]);
  const running = live && live.status && live.status !== "idle";
  const sinceRef = useRef(Date.now());
  useEffect(() => { if (running) sinceRef.current = live.since || Date.now(); }, [running]);
  const lastSteps = items.map((it) => it.type).lastIndexOf("steps");

  const renderItem = (it) => {
    if (it.type === "control") return html`<${Trace} key=${it.msg.id} tag="Objectif non atteint">${controlText(it.msg)}<//>`;
    if (it.type === "note") return html`<div class="note" key=${it.msg.id}>${contentText(it.msg)}</div>`;
    if (it.type === "steps") return html`<${Steps} key=${"s" + it.id} item=${it} running=${running && items.indexOf(it) >= lastSteps - 1} onImage=${onImage} />`;
    return html`<${Markdown} key=${it.msg.id} text=${it.msg.content} />`;
  };

  // ce qui se passe en direct (réflexion, texte en cours, question, travail)
  const liveEls = [
    running && live.thinking && !live.partial ? html`<div class="thinking" key="th">${live.thinking.slice(-600)}</div>` : null,
    running && live.partial ? html`<${Markdown} key="pa" text=${live.partial} streaming />` : null,
    live?.ask ? html`<div class="ask-card" key="ask">
        <span class="tag" style="align-self:flex-start">Question</span>
        <div class="q">${live.ask.question}</div>
        ${live.ask.options?.length ? html`<div class="chips">${live.ask.options.map((o) => html`<button class="chip" onClick=${() => onSend(o)}>${o}</button>`)}</div>` : null}
        <div class="foot">Réponds ci-dessous, Ely reprendra aussitôt.</div>
      </div>` : null,
    running && !live.ask && !live.partial ? html`<div class="working" key="wk">
        <span class="pulse"></span>
        <span class="what">${live.detail || (live.activity ? `Ely travaille · ${live.activity}` : "Ely réfléchit")}</span>
        <${Elapsed} since=${sinceRef.current} />
        <button class="link-btn" onClick=${onCancel}>Arrêter</button>
      </div>` : null,
    live?.notice ? html`<${Trace} key="no" tag="Modèle">${live.notice}<//>` : null,
  ].filter(Boolean);
  const lastBlock = blocks[blocks.length - 1];
  const joinLast = liveEls.length && lastBlock?.type === "turn" && !lastBlock.closed;

  return html`<div class="thread">
    ${blocks.map((b, i) => {
      if (b.type === "user") return html`<${UserMsg} key=${b.msg.id} msg=${b.msg} onImage=${onImage} />`;
      if (b.type === "scheduled") return html`<${Trace} key=${b.msg.id} tag="Tâche planifiée">${contentText(b.msg).replace(/^\[Tâche planifiée #\d+\]\s*/, "")}<//>`;
      if (b.type === "control") return renderItem(b);
      return html`<div class="turn msg" key=${b.key}>
        <${Meta} ts=${b.ts} />
        ${b.items.map(renderItem)}
        ${joinLast && i === blocks.length - 1 ? liveEls : null}
      </div>`;
    })}
    ${liveEls.length && !joinLast ? html`<div class="turn msg" key="live"><${Meta} ts=${Date.now() / 1000} />${liveEls}</div>` : null}
  </div>`;
}

export function Composer({ onSend, running, onCancel, prefill, autoVoice, disabled }) {
  const [text, setText] = useState(prefill || "");
  const [atts, setAtts] = useState([]);
  const [busy, setBusy] = useState(0);
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

  return html`<div class="composer-wrap"><div class="composer-in">
    <div class=${"composer" + (drag ? " drag" : "")}
         onDragOver=${(e) => { e.preventDefault(); setDrag(true); }} onDragLeave=${() => setDrag(false)}
         onDrop=${(e) => { e.preventDefault(); setDrag(false); addFiles([...e.dataTransfer.files]); }}>
      ${atts.length || busy ? html`<div class="attachments">
        ${atts.map((a) => html`<span class="att"><${FileTag} path=${a.name} /><span>${a.name}</span><button title="Retirer" onClick=${() => setAtts(atts.filter((x) => x !== a))}><${Icon} name="close" size=${10} stroke=${1.8} /></button></span>`)}
        ${busy ? html`<span class="att"><div class="spinner"></div><span>Envoi…</span></span>` : null}
      </div>` : null}
      <div class="composer-row">
        <input type="file" multiple ref=${fileRef} style="display:none" onChange=${(e) => { addFiles([...e.target.files]); e.target.value = ""; }} />
        <button class="c-btn" title="Joindre un fichier ou une photo" onClick=${() => fileRef.current.click()}><${Icon} name="plus" /></button>
        <textarea ref=${ta} rows="1" value=${text} disabled=${disabled}
          placeholder=${listening ? "Je t'écoute…" : running ? "Ajoute une précision, Ely en tiendra compte…" : "Demande n'importe quoi à Ely…"}
          onInput=${(e) => setText(e.target.value)} onKeyDown=${onKey} onPaste=${onPaste}></textarea>
        ${running ? html`<button class="c-btn stop" title="Arrêter la tâche" onClick=${onCancel}><${Icon} name="stop" size=${14} /></button>` : null}
        ${text.trim() || atts.length ? html`<button class="c-btn send" title="Envoyer" onClick=${() => send()} disabled=${busy > 0}><${Icon} name="send" stroke=${1.7} /></button>`
          : html`<button class=${"c-btn send mic" + (listening ? " on" : "")} title="Parler à Ely" onClick=${startVoice}><${Icon} name="mic" /></button>`}
      </div>
    </div>
    <div class="hint">Entrée pour envoyer · Maj+Entrée pour aller à la ligne · glisse un fichier pour le joindre</div>
  </div></div>`;
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
      <span class="label">Navigateur</span>
      <span class="url">${img?.url || "—"}</span>
      <button class="icon-btn soft" title="Actualiser" onClick=${refresh}><${Icon} name="refresh" /></button>
      <button class="icon-btn soft" title="Fermer" onClick=${onClose}><${Icon} name="close" /></button>
    </div>
    <div class=${"live-view" + (control ? " control" : "")}>
      ${img?.image ? html`<img src=${"data:image/jpeg;base64," + img.image} onClick=${click} />`
        : html`<div class="live-empty">Le navigateur d'Ely s'affichera ici dès qu'elle l'utilisera.<br/><br/>
            Tu peux aussi l'ouvrir toi-même pour te connecter une fois à tes sites (Doctolib, LinkedIn…).</div>`}
    </div>
    <div class="live-foot">
      <label class="toggle small"><span class="switch"><input type="checkbox" checked=${control} onChange=${(e) => setControl(e.target.checked)} /><span></span></span>
        <span>Prendre la main</span><span class="meta-text">${control ? "touche l'image pour cliquer" : "connexion, captcha…"}</span></label>
      ${control ? html`
        <div class="row nowrap">
          <input class="input" placeholder="Texte à taper" value=${typed} onInput=${(e) => setTyped(e.target.value)}
                 onKeyDown=${(e) => { if (e.key === "Enter") { act("type", { text: typed }); setTyped(""); } }} />
          <button class="btn" onClick=${() => { act("type", { text: typed }); setTyped(""); }}>Taper</button>
        </div>
        <div class="row">
          ${[["Entrée", "Enter"], ["Tab", "Tab"], ["⌫", "Backspace"], ["Échap", "Escape"]].map(([l, k]) => html`<button class="btn" onClick=${() => act("key", { text: k })}>${l}</button>`)}
          <button class="btn" onClick=${() => act("scroll", { y: -1 })}>↑</button>
          <button class="btn" onClick=${() => act("scroll", { y: 1 })}>↓</button>
          <button class="btn" onClick=${() => act("back")}>Retour</button>
        </div>
        <div class="row nowrap">
          <input class="input" placeholder="Aller à… (ex. doctolib.fr)" value=${goto} onInput=${(e) => setGoto(e.target.value)}
                 onKeyDown=${(e) => { if (e.key === "Enter" && goto) act("goto", { text: goto }); }} />
          <button class="btn" onClick=${() => goto && act("goto", { text: goto })}>Ouvrir</button>
        </div>` : null}
    </div>
  </aside>`;
}
