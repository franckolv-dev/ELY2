// Ely — application principale.
import { html, render, useEffect, useRef, useState, useMemo } from "/static/vendor/preact-htm.js";
import { ApiError, connectEvents, del, get, patch, post } from "/static/js/api.js";
import { Composer, LiveBrowser, SUGGESTIONS, Thread } from "/static/js/chat.js";
import { Settings } from "/static/js/settings.js";
import { Icon, groupLabel, isMobile, onToast, toast } from "/static/js/util.js";
import { speak, stopSpeaking } from "/static/js/voice.js";

const theme = localStorage.getItem("ely-theme");
if (theme && theme !== "auto") document.documentElement.setAttribute("data-theme", theme);
const params = new URLSearchParams(location.search);

// ---------------------------------------------------------------- connexion
function Login({ setup, onLogged }) {
  const invite = params.get("invite") || "";
  const [mode, setMode] = useState(setup?.needs_setup || invite ? "register" : "login");
  const [f, setF] = useState({ email: "", password: "", name: "", invite });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await post(mode === "login" ? "/api/auth/login" : "/api/auth/register", f);
      history.replaceState(null, "", "/");
      onLogged(r.user);
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  }
  const first = setup?.needs_setup;
  return html`<div class="auth"><div class="auth-card">
    <div><div class="logo-mark big" style="margin:0 auto"></div><h1>${first ? "Bienvenue dans Ely" : "Ely"}</h1>
      <p class="muted">${first ? "Crée le compte administrateur pour commencer." : "Ton agent personnel. Tu demandes, il agit."}</p></div>
    <form onSubmit=${submit}>
      ${mode === "register" ? html`<label class="field">Prénom<input class="input" required value=${f.name} onInput=${(e) => setF({ ...f, name: e.target.value })} autocomplete="given-name" /></label>` : null}
      <label class="field">E-mail<input class="input" type="email" required value=${f.email} onInput=${(e) => setF({ ...f, email: e.target.value })} autocomplete="email" /></label>
      <label class="field">Mot de passe<input class="input" type="password" required minlength="6" value=${f.password} onInput=${(e) => setF({ ...f, password: e.target.value })}
        autocomplete=${mode === "login" ? "current-password" : "new-password"} /></label>
      ${mode === "register" && !first && !setup?.open_registration ? html`<label class="field">Code d'invitation<input class="input" required value=${f.invite} onInput=${(e) => setF({ ...f, invite: e.target.value })} /></label>` : null}
      ${err ? html`<div class="error-text">${err}</div>` : null}
      <button class="btn primary" disabled=${busy}>${busy ? "…" : mode === "login" ? "Se connecter" : "Créer mon compte"}</button>
      ${!first ? html`<button type="button" class="btn ghost small" onClick=${() => setMode(mode === "login" ? "register" : "login")}>
        ${mode === "login" ? "J'ai une invitation" : "J'ai déjà un compte"}</button>` : null}
    </form>
  </div></div>`;
}

// ---------------------------------------------------------------- barre latérale
function Sidebar({ me, convs, cur, live, open, onPick, onNew, onSettings, onSearch, onChanged, pwa }) {
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(null);
  const timer = useRef();
  const groups = useMemo(() => {
    const out = [];
    const pinned = convs.filter((c) => c.pinned);
    if (pinned.length) out.push(["Épinglées", pinned]);
    for (const c of convs.filter((c) => !c.pinned)) {
      const g = groupLabel(c.updated_at);
      const last = out[out.length - 1];
      if (last && last[0] === g) last[1].push(c); else out.push([g, [c]]);
    }
    return out;
  }, [convs]);
  async function rename(c) {
    const t = prompt("Nouveau titre", c.title);
    if (t) { await patch(`/api/conversations/${c.id}`, { title: t }); onChanged(); }
  }
  return html`<aside class=${"sidebar" + (open ? " open" : "")}>
    <div class="sidebar-head"><div class="brand"><div class="logo-mark"></div>Ely</div></div>
    <button class="btn new-chat" onClick=${onNew}><${Icon} name="plus" size=${18} /> Nouvelle demande</button>
    <div class="search"><${Icon} name="search" /><input class="input" placeholder="Rechercher" value=${q}
      onInput=${(e) => { setQ(e.target.value); clearTimeout(timer.current); timer.current = setTimeout(() => onSearch(e.target.value), 250); }} /></div>
    <div class="conv-list">
      ${groups.map(([label, list]) => html`<div class="conv-group">${label}</div>
        ${list.map((c) => {
          const st = live[c.id]?.status || c.status;
          return html`<div class=${"conv" + (c.id === cur ? " active" : "")} key=${c.id} role="button" onClick=${() => onPick(c.id)}>
            <span class="t">${c.title}</span>
            ${st === "running" ? html`<span class="dot" title="En cours"></span>` : st === "waiting_user" ? html`<span class="dot wait" title="Attend ta réponse"></span>` : null}
            <button class="icon-btn more" style="width:28px;height:28px" onClick=${(e) => { e.stopPropagation(); setMenu(menu === c.id ? null : c.id); }}><${Icon} name="dots" size=${16} /></button>
            ${menu === c.id ? html`<div class="card" style="position:absolute;right:6px;top:34px;z-index:5;padding:6px;gap:2px;min-width:160px;box-shadow:var(--shadow)" onClick=${(e) => e.stopPropagation()}>
              <button class="btn ghost small" style="justify-content:flex-start" onClick=${() => { setMenu(null); rename(c); }}><${Icon} name="edit" size=${15} /> Renommer</button>
              <button class="btn ghost small" style="justify-content:flex-start" onClick=${async () => { setMenu(null); await patch(`/api/conversations/${c.id}`, { pinned: !c.pinned }); onChanged(); }}><${Icon} name="pin" size=${15} /> ${c.pinned ? "Désépingler" : "Épingler"}</button>
              <button class="btn ghost small danger" style="justify-content:flex-start" onClick=${async () => { setMenu(null); if (confirm("Supprimer cette conversation ?")) { await del(`/api/conversations/${c.id}`); onChanged(c.id); } }}><${Icon} name="trash" size=${15} /> Supprimer</button>
            </div>` : null}
          </div>`;
        })}`)}
      ${!convs.length ? html`<div class="faint small" style="padding:16px 10px">Tes conversations apparaîtront ici.</div>` : null}
    </div>
    <div class="sidebar-foot">
      <div class="avatar">${(me.name || "?")[0].toUpperCase()}</div>
      <div class="who"><b>${me.name}</b><span class="faint small">${me.role === "admin" ? "Administrateur" : me.email}</span></div>
      ${pwa ? html`<button class="icon-btn" title="Installer l'application" onClick=${pwa}>📲</button>` : null}
      <button class="icon-btn" title="Réglages" onClick=${() => onSettings("profil")}><${Icon} name="gear" /></button>
    </div>
  </aside>`;
}

// ---------------------------------------------------------------- notifications push
function b64ToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

window.elyEnablePush = async () => {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !isSecureContext) {
    toast("Notifications indisponibles : ouvre Ely en HTTPS (voir le guide d'installation).", 5000); return false;
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast("Notifications refusées."); return false; }
  const reg = await navigator.serviceWorker.ready;
  const { key } = await get("/api/push/key");
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await post("/api/push/subscribe", { subscription: sub.toJSON() });
  toast("Notifications activées 🔔");
  return true;
};

function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => onToast((t, ms) => { setItems((l) => [...l, t]); setTimeout(() => setItems((l) => l.filter((x) => x !== t)), ms); }), []);
  return html`<div class="toasts">${items.map((t) => html`<div class="toast" key=${t.id}>${t.text}</div>`)}</div>`;
}

// ---------------------------------------------------------------- application
function App() {
  const [me, setMe] = useState(undefined);
  const [setup, setSetup] = useState(null);
  const [convs, setConvs] = useState([]);
  const [cur, setCur] = useState(params.get("c") ? parseInt(params.get("c")) : null);
  const [msgs, setMsgs] = useState({});
  const [live, setLive] = useState({});
  const [tools, setTools] = useState({});
  const [models, setModels] = useState([]);
  const [newModel, setNewModel] = useState("");
  const [settingsTab, setSettingsTab] = useState(null);
  const [sideOpen, setSideOpen] = useState(false);
  const [liveOpen, setLiveOpen] = useState(false);
  const [lightbox, setLightbox] = useState(null);
  const [prefill, setPrefill] = useState(null);
  const [handsFree, setHandsFree] = useState(false);
  const [voiceTick, setVoiceTick] = useState(params.get("voice") ? 1 : 0);
  const [pwa, setPwa] = useState(null);
  const [pushBanner, setPushBanner] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const scrollRef = useRef();
  const stick = useRef(true);
  const curRef = useRef(cur);
  const handsRef = useRef(handsFree);
  const liveClosedByUser = useRef(false);
  const deltaBuf = useRef({});
  curRef.current = cur;
  handsRef.current = handsFree;

  // démarrage
  useEffect(() => {
    get("/api/setup").then(setSetup);
    get("/api/me").then(setMe).catch((e) => setMe(e instanceof ApiError && e.status === 401 ? null : null));
    if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
    navigator.serviceWorker?.addEventListener("message", (e) => {
      if (e.data?.type === "open") { const c = new URL(e.data.url).searchParams.get("c"); if (c) pick(parseInt(c)); }
    });
    addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); setPwa(() => () => { e.prompt(); setPwa(null); }); });
  }, []);

  // après connexion
  useEffect(() => {
    if (!me) return;
    loadConvs();
    get("/api/tools").then((l) => setTools(Object.fromEntries(l.map((t) => [t.name, t]))));
    get("/api/models").then((d) => setModels(d.models.filter((m) => m.reachable))).catch(() => {});
    const stop = connectEvents(onEvent);
    if (location.pathname === "/share") {
      const text = [params.get("title"), params.get("text"), params.get("url")].filter(Boolean).join("\n");
      setCur(null); setPrefill(text + "\n\n"); history.replaceState(null, "", "/");
    }
    if (params.get("settings")) {
      setSettingsTab(params.get("settings"));
      if (params.get("ok")) toast(`${params.get("ok")} connecté ✓`);
      if (params.get("error")) toast(`Connexion refusée : ${params.get("error")}`);
      history.replaceState(null, "", "/");
    }
    if (params.get("new")) setCur(null);
    if ("Notification" in window && Notification.permission === "default" && isSecureContext && !localStorage.getItem("ely-push-dismissed")) setPushBanner(true);
    return stop;
  }, [me]);

  // chargement d'une conversation
  useEffect(() => {
    history.replaceState(null, "", cur ? `/?c=${cur}` : "/");
    if (!cur || !me) return;
    stick.current = true;
    get(`/api/conversations/${cur}/messages`).then((d) => {
      setMsgs((m) => ({ ...m, [cur]: d.messages }));
      if (d.state) setLive((l) => ({ ...l, [cur]: { ...d.state, since: l[cur]?.since || Date.now() } }));
    }).catch(() => setCur(null));
  }, [cur, me]);

  // défilement collé en bas
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  function loadConvs(q = "") {
    get(`/api/conversations${q ? "?q=" + encodeURIComponent(q) : ""}`).then(setConvs);
  }

  function upd(cid, fn) {
    setLive((l) => ({ ...l, [cid]: fn(l[cid] || {}) }));
  }

  function flushDeltas() {
    const buf = deltaBuf.current;
    deltaBuf.current = {};
    setLive((l) => {
      const n = { ...l };
      for (const [cid, d] of Object.entries(buf)) {
        const s = n[cid] || {};
        n[cid] = { ...s, partial: (s.partial || "") + (d.text || ""), thinking: (s.thinking || "") + (d.thinking || "") };
      }
      return n;
    });
  }

  function onEvent(ev) {
    const cid = ev.conversation_id;
    switch (ev.type) {
      case "hello":
        setLive((l) => { const n = { ...l }; for (const s of ev.states) n[s.conversation_id] = { ...s, since: Date.now() }; return n; });
        break;
      case "delta": {
        const b = deltaBuf.current;
        if (!Object.keys(b).length) requestAnimationFrame(flushDeltas);
        b[cid] = b[cid] || {};
        const k = ev.kind === "thinking" ? "thinking" : "text";
        b[cid][k] = (b[cid][k] || "") + ev.text;
        break;
      }
      case "message": {
        const m = ev.message;
        setMsgs((all) => {
          const list = all[cid] || [];
          if (list.some((x) => x.id === m.id)) return all;
          return { ...all, [cid]: [...list, m] };
        });
        if (m.role === "assistant") {
          deltaBuf.current = {};
          upd(cid, (s) => ({ ...s, partial: "", thinking: "" }));
          if (!m.tool_calls?.length && m.content && m.kind !== "note" && cid === curRef.current) {
            const again = handsRef.current;
            if (again || localStorage.getItem("ely-read") === "1") speak(m.content, () => again && setVoiceTick((t) => t + 1));
          }
        }
        setConvs((list) => {
          const i = list.findIndex((c) => c.id === cid);
          if (i < 0) { loadConvs(); return list; }
          const c = { ...list[i], updated_at: Date.now() / 1000 };
          return [c, ...list.slice(0, i), ...list.slice(i + 1)];
        });
        break;
      }
      case "stream_reset": upd(cid, (s) => ({ ...s, partial: "" })); break;
      case "status":
        upd(cid, (s) => ({ ...s, status: ev.status === "retrying" ? "running" : ev.status, detail: ev.detail || "", since: s.since || Date.now() }));
        setConvs((l) => l.map((c) => (c.id === cid ? { ...c, status: "running" } : c)));
        break;
      case "tool_start": upd(cid, (s) => ({ ...s, activity: ev.sub ? `sous-agent ${ev.sub} · ${ev.label}` : ev.label, detail: "" })); break;
      case "ask_user":
        upd(cid, (s) => ({ ...s, status: "waiting_user", ask: ev }));
        if (cid === curRef.current && handsRef.current) speak(ev.question, () => setVoiceTick((t) => t + 1));
        break;
      case "model": if (ev.reason) { upd(cid, (s) => ({ ...s, notice: `${ev.reason}` })); } break;
      case "run_end":
        upd(cid, (s) => ({ ...s, status: "idle", partial: "", thinking: "", ask: null, detail: "", activity: "", notice: "" }));
        setConvs((l) => l.map((c) => (c.id === cid ? { ...c, status: "idle" } : c)));
        break;
      case "title": setConvs((l) => l.map((c) => (c.id === cid ? { ...c, title: ev.title } : c))); break;
      case "browser_frame":
        upd(cid, (s) => ({ ...s, frame: { image: ev.image, url: ev.url } }));
        if (cid === curRef.current && !isMobile() && !liveClosedByUser.current) setLiveOpen(true);
        break;
      case "queued": toast("Ajouté à la tâche en cours ✓"); break;
    }
  }

  function pick(id) {
    setCur(id); setSideOpen(false); stopSpeaking();
    liveClosedByUser.current = false;
  }

  async function send(text, attachments = []) {
    stick.current = true;
    const cid = cur;
    try {
      if (cid) upd(cid, (s) => ({ ...s, status: s.status === "idle" || !s.status ? "running" : s.status, since: s.status === "running" ? s.since : Date.now(), ask: null }));
      const r = await post("/api/chat", { text, attachments, conversation_id: cid || undefined, model: cid ? undefined : newModel || undefined });
      if (!cid) {
        upd(r.conversation_id, (s) => ({ ...s, status: "running", since: Date.now() }));
        setCur(r.conversation_id); loadConvs();
      }
    } catch (e) { toast(e.message); }
  }

  async function cancel() {
    if (cur) await post(`/api/conversations/${cur}/cancel`);
  }

  async function setModel(value) {
    if (cur) { await patch(`/api/conversations/${cur}`, { model: value }); setConvs((l) => l.map((c) => (c.id === cur ? { ...c, model: value } : c))); }
    else setNewModel(value);
    toast(value ? `Modèle : ${value}` : "Choix automatique du modèle");
  }

  if (me === undefined) return html`<div class="boot"><div class="logo-mark big"></div></div>`;
  if (me === null) return html`<${Login} setup=${setup} onLogged=${setMe} /><${Toasts} />`;

  const conv = convs.find((c) => c.id === cur);
  const state = live[cur] || {};
  const running = state.status && state.status !== "idle";
  const messages = cur ? msgs[cur] : null;
  const frame = state.frame;
  const hour = new Date().getHours();
  const hello = hour < 5 ? "Bonne nuit" : hour < 18 ? "Bonjour" : "Bonsoir";

  return html`<div class="layout">
    <${Sidebar} me=${me} convs=${convs} cur=${cur} live=${live} open=${sideOpen} pwa=${pwa}
      onPick=${pick} onNew=${() => pick(null)} onSettings=${(t) => { setSettingsTab(t); setSideOpen(false); }}
      onSearch=${loadConvs} onChanged=${(deleted) => { if (deleted === cur) setCur(null); loadConvs(); }} />
    <div class=${"scrim" + (sideOpen ? " open" : "")} onClick=${() => setSideOpen(false)}></div>
    <main class="main">
      <header class=${"topbar" + (scrolled ? " scrolled" : "")}>
        <button class="icon-btn menu-btn" onClick=${() => setSideOpen(true)} title="Conversations"><${Icon} name="menu" /></button>
        <div class="title">${conv ? conv.title : "Nouvelle demande"}</div>
        <select class="model-select" title="Modèle" value=${cur ? conv?.model || "" : newModel} onChange=${(e) => setModel(e.target.value)}>
          <option value="">✨ Auto</option>
          ${models.map((m) => html`<option value=${m.ref}>${m.ref.replace(/^[^:]+:/, "")} · ${m.provider}</option>`)}
        </select>
        <button class=${"icon-btn" + (handsFree ? " mic on" : "")} title="Mode mains libres (conversation vocale)"
          onClick=${() => { const v = !handsFree; setHandsFree(v); if (v) { toast("Mode mains libres : parle, Ely répond à voix haute"); setVoiceTick((t) => t + 1); } else stopSpeaking(); }}>
          <${Icon} name="speaker" /></button>
        <button class="icon-btn" title="Navigateur d'Ely" onClick=${() => { const v = !liveOpen; setLiveOpen(v); liveClosedByUser.current = !v; }}><${Icon} name="globe" /></button>
      </header>
      <div class="content">
        <section class="chat">
          <div class="scroll" ref=${scrollRef} onScroll=${(e) => {
            const el = e.target; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140; setScrolled(el.scrollTop > 4);
          }}>
            ${pushBanner ? html`<div class="banner" style="margin-top:10px"><span>🔔</span><span class="grow">Active les notifications pour être prévenu quand Ely a fini ou a besoin de toi.</span>
              <button class="btn small primary" onClick=${async () => { await window.elyEnablePush(); setPushBanner(false); }}>Activer</button>
              <button class="icon-btn" onClick=${() => { localStorage.setItem("ely-push-dismissed", "1"); setPushBanner(false); }}><${Icon} name="close" size=${16} /></button></div>` : null}
            ${!cur ? html`<div class="welcome">
                <div><h1>${hello} <span>${me.name}</span>,<br/>que puis-je faire pour toi ?</h1></div>
                <p>Demande-moi une information ou une vraie action : je m'en occupe jusqu'au bout, même quand l'application est fermée.</p>
                <div class="suggestions">${SUGGESTIONS.map(([t, p]) => html`<button class="suggestion" onClick=${() => setPrefill(p + " ")}><b>${t}</b><span>${p}</span></button>`)}</div>
              </div>`
              : messages ? html`<${Thread} messages=${messages} live=${state} tools=${tools} onSend=${send} onImage=${setLightbox} onCancel=${cancel} />`
              : html`<div class="boot" style="height:50vh"><div class="spinner"></div></div>`}
          </div>
          ${frame && !liveOpen ? html`<div class="live-mini" onClick=${() => { setLiveOpen(true); liveClosedByUser.current = false; }}>
            <img src=${"data:image/jpeg;base64," + frame.image} /><span>EN DIRECT</span></div>` : null}
          <${Composer} onSend=${send} running=${running} onCancel=${cancel} prefill=${prefill} autoVoice=${voiceTick} />
        </section>
        ${liveOpen ? html`<${LiveBrowser} frame=${frame} conversationId=${cur} mobile=${isMobile()}
          onClose=${() => { setLiveOpen(false); liveClosedByUser.current = true; }} />` : null}
      </div>
    </main>
    ${settingsTab ? html`<${Settings} me=${me} onMe=${setMe} tab=${settingsTab} onTab=${setSettingsTab} onClose=${() => setSettingsTab(null)} pwa=${pwa}
      openConversation=${(id) => { loadConvs(); pick(id); }} />` : null}
    ${lightbox ? html`<div class="lightbox" onClick=${() => setLightbox(null)}><img src=${lightbox} /></div>` : null}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
