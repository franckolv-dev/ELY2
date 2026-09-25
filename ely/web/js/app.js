// Ely — application principale.
import { html, render, useEffect, useRef, useState, useMemo } from "/static/vendor/preact-htm.js";
import { ApiError, connectEvents, del, get, patch, post } from "/static/js/api.js";
import { Composer, LiveBrowser, SUGGESTIONS, Thread } from "/static/js/chat.js";
import { Settings } from "/static/js/settings.js";
import { Icon, applyAccent, applyTheme, clock, groupLabel, isMac, isMobile, onToast, prefersDark, storedAccent, storedTheme, toast } from "/static/js/util.js";
import { speak, stopSpeaking } from "/static/js/voice.js";

applyTheme(storedTheme(), false);
applyAccent(storedAccent(), false);
const KBD_SEARCH = isMac ? "⌘K" : "Ctrl K";
const KBD_NEW = isMac ? "⇧⌘O" : "Ctrl ⇧O";
const LOCAL = new Set(["lmstudio", "ollama"]);
const shortModel = (ref) => (ref || "").replace(/^[^:]+:/, "").split("/").pop();
const params = new URLSearchParams(location.search);

// ---------------------------------------------------------------- connexion
function Login({ setup, onLogged }) {
  const invite = params.get("invite") || "";
  const [mode, setMode] = useState(setup?.needs_setup || invite ? "register" : "login");
  useEffect(() => { if (setup?.needs_setup) setMode("register"); }, [setup?.needs_setup]);
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
    <div><div class="logo-mark big"></div><h1>${first ? "Bienvenue dans Ely" : "Ely"}</h1>
      <p>${first ? "Crée le compte administrateur pour commencer." : "Ton agent personnel. Tu demandes, il agit."}</p></div>
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
function Sidebar({ me, convs, cur, live, open, badge, onPick, onNew, onSettings, onSearch, onChanged, pwa }) {
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
  useEffect(() => {
    if (menu === null) return;
    const close = () => setMenu(null);
    addEventListener("click", close);
    return () => removeEventListener("click", close);
  }, [menu]);
  async function rename(c) {
    const t = prompt("Nouveau titre", c.title);
    if (t) { await patch(`/api/conversations/${c.id}`, { title: t }); onChanged(); }
  }
  return html`<aside class=${"sidebar" + (open ? " open" : "")}>
    <div class="brand"><div class="logo-mark"></div><span class="brand-name">ely</span>
      ${badge ? html`<span class="badge" title=${badge.title}>${badge.text}</span>` : null}</div>
    <div class="side-actions">
      <button class="new-chat" onClick=${onNew}><${Icon} name="plus" size=${14} stroke=${1.6} /><span>Nouvelle demande</span><span class="kbd">${KBD_NEW}</span></button>
      <label class="search"><${Icon} name="search" size=${14} /><input placeholder="Rechercher" value=${q}
        onInput=${(e) => { setQ(e.target.value); clearTimeout(timer.current); timer.current = setTimeout(() => onSearch(e.target.value), 250); }} />
        <span class="kbd">${KBD_SEARCH}</span></label>
    </div>
    <div class="conv-list">
      ${groups.map(([label, list]) => html`<div class="conv-group label">${label}</div>
        ${list.map((c) => {
          const st = live[c.id]?.status || c.status;
          const active = c.id === cur;
          const dot = st === "running" ? "conv-dot run" : st === "waiting_user" ? "conv-dot wait" : active ? "conv-dot" : "";
          return html`<div class=${"conv" + (active ? " active" : "") + (menu === c.id ? " menu-open" : "")} key=${c.id} role="button" onClick=${() => onPick(c.id)}
              title=${st === "running" ? "En cours" : st === "waiting_user" ? "Attend ta réponse" : ""}>
            ${dot ? html`<span class=${dot}></span>` : null}
            <span class="t">${c.title}</span>
            <span class="when">${clock(c.updated_at)}</span>
            <button class="icon-btn more" title="Options" onClick=${(e) => { e.stopPropagation(); setMenu(menu === c.id ? null : c.id); }}><${Icon} name="dots" size=${16} stroke=${2.2} /></button>
            ${menu === c.id ? html`<div class="popover" onClick=${(e) => e.stopPropagation()}>
              <button onClick=${() => { setMenu(null); rename(c); }}><${Icon} name="edit" size=${14} /> Renommer</button>
              <button onClick=${async () => { setMenu(null); await patch(`/api/conversations/${c.id}`, { pinned: !c.pinned }); onChanged(); }}><${Icon} name="pin" size=${14} /> ${c.pinned ? "Désépingler" : "Épingler"}</button>
              <button class="danger" onClick=${async () => { setMenu(null); if (confirm("Supprimer cette conversation ?")) { await del(`/api/conversations/${c.id}`); onChanged(c.id); } }}><${Icon} name="trash" size=${14} /> Supprimer</button>
            </div>` : null}
          </div>`;
        })}`)}
      ${!convs.length ? html`<div class="side-empty">${q ? "Aucun résultat." : "Tes conversations apparaîtront ici."}</div>` : null}
    </div>
    <div class="sidebar-foot">
      <div class="avatar">${(me.name || "?")[0].toUpperCase()}</div>
      <div class="who"><b>${me.name}</b><span>${me.role === "admin" ? "Administrateur" : me.email}</span></div>
      ${pwa ? html`<button class="icon-btn" title="Installer l'application" onClick=${pwa}><${Icon} name="phone" /></button>` : null}
      <button class="icon-btn" title="Réglages" onClick=${() => onSettings("profil")}><${Icon} name="gear" size=${16} stroke=${1.4} /></button>
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
  toast("Notifications activées");
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
  const [noModel, setNoModel] = useState(false);
  const [mainRef, setMainRef] = useState("");
  const [theme, setTheme] = useState(storedTheme());
  const [accent, setAccent] = useState(storedAccent());
  const [, setSystemDark] = useState(prefersDark());
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
    get("/api/models").then((d) => { setModels(d.models.filter((m) => m.reachable)); setMainRef(d.roles.main?.effective || ""); setNoModel(!d.roles.main?.effective); }).catch(() => {});
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

  // raccourcis clavier : ⌘K recherche, ⇧⌘O nouvelle demande ; suivi du thème du système
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "k" && !e.shiftKey) { e.preventDefault(); setSideOpen(true); document.querySelector(".search input")?.focus(); }
      else if (k === "o" && e.shiftKey) { e.preventDefault(); pick(null); }
    };
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onMq = () => setSystemDark(mq.matches);
    addEventListener("keydown", onKey);
    mq.addEventListener("change", onMq);
    return () => { removeEventListener("keydown", onKey); mq.removeEventListener("change", onMq); };
  }, []);

  function chooseTheme(v) { applyTheme(v); setTheme(v); }
  function chooseAccent(v) { applyAccent(v); setAccent(v); }

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
      case "hello": {
        // (re)connexion : l'état du serveur fait foi ; ce qui n'y figure plus est terminé
        const active = new Set(ev.states.map((s) => s.conversation_id));
        setLive((l) => {
          const n = {};
          for (const [k, v] of Object.entries(l)) n[k] = { ...v, status: "idle", partial: "", thinking: "", ask: null, detail: "", activity: "", notice: "" };
          for (const s of ev.states) n[s.conversation_id] = { ...(n[s.conversation_id] || {}), ...s, since: l[s.conversation_id]?.since || Date.now() };
          return n;
        });
        setConvs((list) => list.map((c) => (active.has(c.id) ? c : { ...c, status: "idle" })));
        const c = curRef.current;
        if (c) get(`/api/conversations/${c}/messages`).then((d) => setMsgs((m) => ({ ...m, [c]: d.messages }))).catch(() => {});
        break;
      }
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
        upd(cid, (s) => ({ ...s, status: ev.status === "retrying" ? "running" : ev.status, detail: ev.detail || "", since: s.since || Date.now(),
          ask: ev.status === "waiting_user" ? s.ask : null }));
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

  // attendre les deux réponses : sinon l'écran de connexion s'afficherait avant de savoir s'il faut créer le premier compte
  if (me === undefined || (me === null && !setup)) return html`<div class="boot"><div class="logo-mark big"></div></div>`;
  if (me === null) return html`<${Login} setup=${setup} onLogged=${setMe} /><${Toasts} />`;

  const conv = convs.find((c) => c.id === cur);
  const state = live[cur] || {};
  const running = state.status && state.status !== "idle";
  const messages = cur ? msgs[cur] : null;
  const frame = state.frame;
  const hour = new Date().getHours();
  const hello = hour < 5 ? "Bonne nuit" : hour < 18 ? "Bonjour" : "Bonsoir";
  const dark = theme === "dark" || (theme === "auto" && prefersDark());
  const chosen = cur ? conv?.model || "" : newModel;
  const mainProvider = mainRef.split(":")[0];
  const badge = mainRef ? { text: LOCAL.has(mainProvider) ? "local" : mainProvider, title: `Modèle principal : ${mainRef}` } : null;

  return html`<div class="layout">
    <${Sidebar} me=${me} convs=${convs} cur=${cur} live=${live} open=${sideOpen} pwa=${pwa} badge=${badge}
      onPick=${pick} onNew=${() => pick(null)} onSettings=${(t) => { setSettingsTab(t); setSideOpen(false); }}
      onSearch=${loadConvs} onChanged=${(deleted) => { if (deleted === cur) setCur(null); loadConvs(); }} />
    <div class=${"scrim" + (sideOpen ? " open" : "")} onClick=${() => setSideOpen(false)}></div>
    <main class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" onClick=${() => setSideOpen(true)} title="Conversations"><${Icon} name="menu" size=${18} /></button>
        <h1>${conv ? conv.title : "Nouvelle demande"}</h1>
        <div class="top-actions">
          <label class="model-pill" title=${chosen ? `Modèle imposé : ${chosen}` : mainRef ? `Choix automatique (${mainRef})` : "Choix automatique du modèle"}>
            <span class="dot"></span><span class="name">${chosen ? shortModel(chosen) : mainRef ? shortModel(mainRef) : "auto"}</span>
            <span class="chev"><${Icon} name="chev" size=${10} stroke=${1.4} /></span>
            <select value=${chosen} onChange=${(e) => setModel(e.target.value)} aria-label="Modèle">
              <option value="">Automatique${mainRef ? ` (${shortModel(mainRef)})` : ""}</option>
              ${models.map((m) => html`<option value=${m.ref}>${shortModel(m.ref)} · ${m.provider}</option>`)}
            </select>
          </label>
          <button class=${"round-btn" + (handsFree ? " on" : "")} title="Mode mains libres (conversation vocale)"
            onClick=${() => { const v = !handsFree; setHandsFree(v); if (v) { toast("Mode mains libres : parle, Ely répond à voix haute"); setVoiceTick((t) => t + 1); } else stopSpeaking(); }}>
            <${Icon} name="speaker" size=${15} stroke=${1.4} /></button>
          <button class=${"round-btn" + (liveOpen ? " on" : "")} title="Navigateur d'Ely" onClick=${() => { const v = !liveOpen; setLiveOpen(v); liveClosedByUser.current = !v; }}>
            <${Icon} name="globe" size=${15} stroke=${1.3} /></button>
          <button class="round-btn" title=${dark ? "Passer en clair" : "Passer en sombre"} onClick=${() => chooseTheme(dark ? "light" : "dark")}>
            <${Icon} name=${dark ? "sun" : "moon"} size=${15} stroke=${1.4} /></button>
        </div>
      </header>
      <div class="content">
        <section class="chat">
          <div class="scroll" ref=${scrollRef} onScroll=${(e) => {
            const el = e.target; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
          }}>
            ${pushBanner ? html`<div class="banner"><span class="grow">Active les notifications pour être prévenu quand Ely a fini ou a besoin de toi.</span>
              <button class="btn small accent" onClick=${async () => { await window.elyEnablePush(); setPushBanner(false); }}>Activer</button>
              <button class="icon-btn soft" title="Plus tard" onClick=${() => { localStorage.setItem("ely-push-dismissed", "1"); setPushBanner(false); }}><${Icon} name="close" size=${14} /></button></div>` : null}
            ${noModel ? html`<div class="banner warn"><span class="tag">Aucun modèle</span>
              <span class="grow">Ajoute une clé d'API dans le fichier <code>.env</code> ou lance LM Studio.</span>
              ${me.role === "admin" ? html`<button class="btn small" onClick=${() => setSettingsTab("modeles")}>Modèles</button>` : null}</div>` : null}
            ${!cur ? html`<div class="welcome">
                <h1>${hello} ${me.name},<br/><em>que puis-je faire pour toi ?</em></h1>
                <p>Demande une information ou une vraie action : Ely s'en occupe jusqu'au bout, même quand l'application est fermée.</p>
                <div class="chips">${SUGGESTIONS.map(([t, p]) => html`<button class="chip" title=${p} onClick=${() => setPrefill(p + " ")}>${t}</button>`)}</div>
              </div>`
              : messages ? html`<${Thread} messages=${messages} live=${state} onSend=${send} onImage=${setLightbox} onCancel=${cancel} />`
              : html`<div class="boot" style="height:50vh"><div class="spinner big"></div></div>`}
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
      prefs=${{ theme, accent, setTheme: chooseTheme, setAccent: chooseAccent }}
      openConversation=${(id) => { loadConvs(); pick(id); }} />` : null}
    ${lightbox ? html`<div class="lightbox" onClick=${() => setLightbox(null)}><img src=${lightbox} /></div>` : null}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
