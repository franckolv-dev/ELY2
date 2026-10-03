// Ely — application principale.
import { html, render, useEffect, useRef, useState, useMemo } from "/static/vendor/preact-htm.js";
import { ApiError, connectEvents, del, get, patch, post } from "/static/js/api.js";
import { Composer, LiveBrowser, Thread } from "/static/js/chat.js";
import { LANGS, getLang, setLang, t } from "/static/js/i18n.js";
import { ExtensionDialog, InstallDialog, isStandalone, onPhone } from "/static/js/install.js";
import { Settings } from "/static/js/settings.js";
import { Icon, Logo, applyTheme, isMac, isMobile, onToast, prefersDark, storedTheme, toast } from "/static/js/util.js";
import { loadRecordedVoices, speak, stopSpeaking } from "/static/js/voice.js";

applyTheme(storedTheme(), false);
const KBD_SEARCH = isMac ? "⌘K" : "Ctrl K";
const params = new URLSearchParams(location.search);

// noms lisibles des fournisseurs de modèles
const PROVIDERS = {
  chatgpt: "ChatGPT", anthropic: "Claude", openai: "OpenAI", gemini: "Gemini", mistral: "Mistral", deepseek: "DeepSeek",
  openrouter: "OpenRouter", groq: "Groq", xai: "Grok", moonshot: "Kimi", qwen: "Qwen", zhipu: "GLM", cerebras: "Cerebras",
  together: "Together", lmstudio: "LM Studio", ollama: "Ollama", custom: "Perso",
};

export function modelLabel(ref, full = false) {
  const [prov, ...rest] = (ref || "").split(":");
  const short = rest.join(":").split("/").pop();
  if (prov === "chatgpt") return `ChatGPT — ${t("model.subscription")}${full ? " · " + short : ""}`;
  return `${PROVIDERS[prov] || prov} — ${short}`;
}

const initials = (name) => {
  const words = (name || "?").trim().split(/\s+/);
  return (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
};

// sélecteurs communs à l'écran de connexion et à l'en-tête
const ThemeToggle = ({ dark, onTheme }) => html`<button class="icon-btn" title=${dark ? t("theme.toLight") : t("theme.toDark")}
  onClick=${() => onTheme(dark ? "light" : "dark")}><${Icon} name=${dark ? "sun" : "moon"} size=${20} /></button>`;

const LangSelect = ({ lang, onLang }) => html`<select class="lang-select" aria-label=${t("lang.label")} value=${lang}
  onChange=${(e) => onLang(e.target.value)}>${LANGS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}</select>`;

// ---------------------------------------------------------------- connexion
function Login({ setup, onLogged, dark, onTheme, lang, onLang }) {
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
  const [title, sub] = first ? [t("setup.title"), t("setup.subtitle")]
    : mode === "register" ? [t("register.title"), t("register.subtitle")] : [t("login.title"), t("login.subtitle")];
  return html`<div class="auth">
    <aside class="auth-side">
      <${Logo} />
      <div class="auth-pitch">
        <h1>${t("login.pitch1")}<br />${t("login.pitch2")}</h1>
        <p>${t("login.pitchSub1")}<br />${t("login.pitchSub2")}</p>
      </div>
      <div class="auth-foot">${t("login.foot")}</div>
    </aside>
    <main class="auth-main">
      <div class="auth-tools"><${ThemeToggle} dark=${dark} onTheme=${onTheme} /><${LangSelect} lang=${lang} onLang=${onLang} /></div>
      <form class="auth-form" onSubmit=${submit}>
        <h2>${title}</h2>
        <p class="sub">${sub}</p>
        ${mode === "register" ? html`<label class="field">${t("login.firstName")}<input class="input" required value=${f.name}
          onInput=${(e) => setF({ ...f, name: e.target.value })} autocomplete="given-name" /></label>` : null}
        <label class="field">${t("login.email")}<input class="input" type="email" required value=${f.email}
          onInput=${(e) => setF({ ...f, email: e.target.value })} autocomplete="email" /></label>
        <label class="field">${t("login.password")}<input class="input" type="password" required minlength="6" value=${f.password}
          onInput=${(e) => setF({ ...f, password: e.target.value })} autocomplete=${mode === "login" ? "current-password" : "new-password"} /></label>
        ${mode === "register" && !first && !setup?.open_registration ? html`<label class="field">${t("login.invite")}<input class="input" required
          value=${f.invite} onInput=${(e) => setF({ ...f, invite: e.target.value })} /></label>` : null}
        ${err ? html`<div class="error-text" style="margin-bottom:12px">${err}</div>` : null}
        <button class="btn primary wide" disabled=${busy}><${Icon} name="arrowRight" size=${16} />
          ${busy ? "…" : mode === "login" ? t("login.submit") : t("login.create")}</button>
        ${mode === "login" ? html`<p class="auth-note">${t("login.note")}</p>` : null}
        ${!first ? html`<button type="button" class="link-btn auth-switch" onClick=${() => setMode(mode === "login" ? "register" : "login")}>
          ${mode === "login" ? t("login.haveInvite") : t("login.haveAccount")}</button>` : null}
      </form>
    </main>
  </div>`;
}

// ---------------------------------------------------------------- barre latérale
function Sidebar({ me, version, convs, cur, live, open, onPick, onNew, onSettings, onSearch, onChanged, onInstall, onExtension }) {
  const [q, setQ] = useState("");
  const [menu, setMenu] = useState(null);
  const [userMenu, setUserMenu] = useState(false);
  const timer = useRef();
  const groups = useMemo(() => {
    const pinned = convs.filter((c) => c.pinned);
    const rest = convs.filter((c) => !c.pinned);
    return [[t("side.pinned"), pinned], [t("side.recent"), rest]].filter(([, list]) => list.length);
  }, [convs, getLang()]);
  useEffect(() => {
    if (menu === null && !userMenu) return;
    const close = () => { setMenu(null); setUserMenu(false); };
    addEventListener("click", close);
    return () => removeEventListener("click", close);
  }, [menu, userMenu]);
  async function rename(c) {
    const title = prompt(t("side.renamePrompt"), c.title);
    if (title) { await patch(`/api/conversations/${c.id}`, { title }); onChanged(); }
  }
  async function remove(c) {
    setMenu(null);
    if (confirm(t("side.deleteConfirm"))) { await del(`/api/conversations/${c.id}`); onChanged(c.id); }
  }
  const logout = async () => { await post("/api/auth/logout"); location.href = "/"; };
  return html`<aside class=${"sidebar" + (open ? " open" : "")}>
    <${Logo} />
    <button class="new-chat" onClick=${onNew}><${Icon} name="plus" size=${18} />${t("side.newChat")}</button>
    <label class="search"><${Icon} name="search" size=${14} /><input placeholder=${t("side.search")} value=${q}
      onInput=${(e) => { setQ(e.target.value); clearTimeout(timer.current); timer.current = setTimeout(() => onSearch(e.target.value), 250); }} />
      <span class="kbd">${KBD_SEARCH}</span></label>
    <div class="conv-list">
      ${groups.map(([label, list]) => html`<div class="conv-group">${label}</div>
        ${list.map((c) => {
          const st = live[c.id]?.status || c.status;
          const dot = st === "running" ? "conv-dot run" : st === "waiting_user" ? "conv-dot wait" : "";
          return html`<div class=${"conv" + (c.id === cur ? " active" : "") + (menu === c.id ? " menu-open" : "")} key=${c.id} role="button"
              onClick=${() => onPick(c.id)} title=${st === "running" ? t("side.running") : st === "waiting_user" ? t("side.waiting") : c.title}>
            <span class="t">${c.title}</span>
            ${dot ? html`<span class=${dot}></span>` : null}
            <button class="icon-btn more" title=${t("common.delete")} onClick=${(e) => { e.stopPropagation(); remove(c); }}>
              <${Icon} name="trash" size=${14} /></button>
            <button class="icon-btn more" title=${t("side.options")} onClick=${(e) => { e.stopPropagation(); setUserMenu(false); setMenu(menu === c.id ? null : c.id); }}>
              <${Icon} name="dots" size=${16} stroke=${2.2} /></button>
            ${menu === c.id ? html`<div class="popover" onClick=${(e) => e.stopPropagation()}>
              <button onClick=${() => { setMenu(null); rename(c); }}><${Icon} name="edit" size=${14} /> ${t("side.rename")}</button>
              <button onClick=${async () => { setMenu(null); await patch(`/api/conversations/${c.id}`, { pinned: !c.pinned }); onChanged(); }}>
                <${Icon} name="pin" size=${14} /> ${c.pinned ? t("side.unpin") : t("side.pin")}</button>
              <button class="danger" onClick=${() => remove(c)}>
                <${Icon} name="trash" size=${14} /> ${t("common.delete")}</button>
            </div>` : null}
          </div>`;
        })}`)}
      ${!convs.length ? html`<div class="side-empty">${q ? t("side.noResult") : t("side.empty")}</div>` : null}
    </div>
    <div class="side-bottom">
      <button class="settings-btn" onClick=${() => onSettings("profil")}><${Icon} name="gear" size=${20} />${t("side.settings")}</button>
      <div class="sidebar-foot">
        <button class="who-btn" title=${t("side.account")} aria-haspopup="menu" aria-expanded=${userMenu}
          onClick=${(e) => { e.stopPropagation(); setMenu(null); setUserMenu(!userMenu); }}>
          <div class="avatar">${initials(me.name)}</div>
          <div class="who"><b>${me.name}</b><span>${t("side.personal")}</span></div>
        </button>
        <button class="icon-btn" title=${t("side.logout")} onClick=${logout}><${Icon} name="logout" size=${18} /></button>
        ${userMenu ? html`<div class="popover up" role="menu" onClick=${(e) => e.stopPropagation()}>
          <div class="popover-head">${me.email}${version ? html`<span class="ver">${t("side.version", { v: version })}</span>` : null}</div>
          ${!isStandalone() ? html`<button role="menuitem" title=${t("side.installTip")} onClick=${() => { setUserMenu(false); onInstall(); }}>
            <${Icon} name="install" size=${16} /> ${t("side.install")}</button>` : null}
          ${!onPhone() ? html`<button role="menuitem" title=${t("side.extensionTip")} onClick=${() => { setUserMenu(false); onExtension(); }}>
            <${Icon} name="puzzle" size=${16} /> ${t("side.extension")}</button>` : null}
        </div>` : null}
      </div>
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
    toast(t("push.unavailable"), 5000); return false;
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast(t("push.denied")); return false; }
  const reg = await navigator.serviceWorker.ready;
  const { key } = await get("/api/push/key");
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await post("/api/push/subscribe", { subscription: sub.toJSON() });
  toast(t("push.on"));
  return true;
};

function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => onToast((item, ms) => { setItems((l) => [...l, item]); setTimeout(() => setItems((l) => l.filter((x) => x !== item)), ms); }), []);
  return html`<div class="toasts">${items.map((item) => html`<div class="toast" key=${item.id}>${item.text}</div>`)}</div>`;
}

// sélecteur de modèle : en-tête (texte) ou barre de saisie (pastille)
const ModelSelect = ({ variant, models, chosen, mainRef, onModel }) => {
  const label = chosen ? modelLabel(chosen) : mainRef ? modelLabel(mainRef) : t("common.automatic");
  const title = chosen ? t("head.modelSet", { m: chosen }) : mainRef ? t("head.modelAuto", { m: mainRef }) : t("head.modelAutoShort");
  return html`<label class=${variant === "chip" ? "model-chip" : "model-pick"} title=${title}>
    ${variant === "chip" ? null : html`<span class="dot"></span>`}
    <span class="name">${label}</span><${Icon} name="chev" size=${12} />
    <select value=${chosen} onChange=${(e) => onModel(e.target.value)} aria-label=${t("head.model")}>
      <option value="">${t("common.automatic")}${mainRef ? ` (${modelLabel(mainRef)})` : ""}</option>
      ${models.map((m) => html`<option value=${m.ref}>${modelLabel(m.ref, true)}</option>`)}
    </select>
  </label>`;
};

const CARDS = [["calendar", "rdv"], ["mail", "mail"], ["doc", "post"], ["tasks", "day"]];

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
  const [dialog, setDialog] = useState(null);
  const [pushBanner, setPushBanner] = useState(false);
  const [noModel, setNoModel] = useState(false);
  const [mainRef, setMainRef] = useState("");
  const [theme, setTheme] = useState(storedTheme());
  const [lang, setLangState] = useState(getLang());
  const [, setSystemDark] = useState(prefersDark());
  const scrollRef = useRef();
  const stick = useRef(true);
  const curRef = useRef(cur);
  const handsRef = useRef(handsFree);
  const liveClosedByUser = useRef(false);
  const deltaBuf = useRef({});
  const codeRef = useRef(null); // version du serveur au chargement de la page
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

  // modèles du menu de conversation : relus à la fermeture des réglages (clé ajoutée, modèle principal changé…)
  const loadModels = () => get("/api/models").then((d) => {
    setModels(d.models.filter((m) => m.reachable)); setMainRef(d.roles.main?.effective || ""); setNoModel(!d.roles.main?.effective);
  }).catch(() => {});

  // après connexion
  useEffect(() => {
    if (!me) return;
    loadConvs();
    loadRecordedVoices(); // voix clonée du Mac, si son service tourne
    loadModels();
    const stop = connectEvents(onEvent);
    if (location.pathname === "/share") {
      const text = [params.get("title"), params.get("text"), params.get("url")].filter(Boolean).join("\n");
      setCur(null); setPrefill(text + "\n\n"); history.replaceState(null, "", "/");
    }
    if (params.get("settings")) {
      setSettingsTab(params.get("settings"));
      if (params.get("ok")) toast(t("oauth.ok", { name: params.get("ok") }));
      if (params.get("error")) toast(t("oauth.refused", { e: params.get("error") }));
      history.replaceState(null, "", "/");
    }
    if (params.get("new")) setCur(null);
    if ("Notification" in window && Notification.permission === "default" && isSecureContext && !localStorage.getItem("ely-push-dismissed")) setPushBanner(true);
    return stop;
  }, [me]);

  // raccourcis clavier : ⌘K recherche, ⇧⌘O nouvelle conversation ; suivi du thème du système
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
  function chooseLang(v) { setLang(v); setLangState(v); }

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
        // Ely a redémarré sur une autre version (mise à jour, auto-amélioration) : cette page porte l'ancienne interface
        if (ev.code && codeRef.current && ev.code !== codeRef.current) {
          const draft = document.querySelector(".composer textarea")?.value.trim() || document.querySelector(".sheet");
          if (draft) toast(t("app.newVersion"), 15000);
          else location.reload();
        }
        if (ev.code) codeRef.current = ev.code;
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
            if (again || localStorage.getItem("ely-read") === "1") speak(m.content, () => again && setVoiceTick((x) => x + 1));
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
      case "tool_start": upd(cid, (s) => ({ ...s, activity: ev.sub ? t("thread.subagent", { n: ev.sub, a: ev.label }) : ev.label, detail: "" })); break;
      case "ask_user":
        upd(cid, (s) => ({ ...s, status: "waiting_user", ask: ev }));
        if (cid === curRef.current && handsRef.current) speak(ev.question, () => setVoiceTick((x) => x + 1));
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
      case "queued": toast(t("run.queued")); break;
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
    toast(value ? t("head.modelToast", { m: modelLabel(value, true) }) : t("head.modelAutoShort"));
  }

  const dark = theme === "dark" || (theme === "auto" && prefersDark());

  // attendre les deux réponses : sinon l'écran de connexion s'afficherait avant de savoir s'il faut créer le premier compte
  if (me === undefined || (me === null && !setup)) return html`<div class="boot"><${Logo} size=${34} word=${false} /></div>`;
  if (me === null) return html`<${Login} setup=${setup} onLogged=${setMe} dark=${dark} onTheme=${chooseTheme} lang=${lang} onLang=${chooseLang} /><${Toasts} />`;

  const conv = convs.find((c) => c.id === cur);
  const state = live[cur] || {};
  const running = state.status && state.status !== "idle";
  const messages = cur ? msgs[cur] : null;
  const frame = state.frame;
  const chosen = cur ? conv?.model || "" : newModel;
  const modelProps = { models, chosen, mainRef, onModel: setModel };
  const composer = (hero) => html`<${Composer} hero=${hero} onSend=${send} running=${running} onCancel=${cancel} prefill=${prefill} autoVoice=${voiceTick}
    extra=${html`<${ModelSelect} variant="chip" ...${modelProps} />`} />`;

  return html`<div class="layout">
    <${Sidebar} me=${me} version=${setup?.version} convs=${convs} cur=${cur} live=${live} open=${sideOpen}
      onInstall=${() => (pwa ? pwa() : setDialog("install"))} onExtension=${() => { setDialog("extension"); setSideOpen(false); }}
      onPick=${pick} onNew=${() => pick(null)} onSettings=${(tab) => { setSettingsTab(tab); setSideOpen(false); }}
      onSearch=${loadConvs} onChanged=${(deleted) => { if (deleted === cur) setCur(null); loadConvs(); }} />
    <div class=${"scrim" + (sideOpen ? " open" : "")} onClick=${() => setSideOpen(false)}></div>
    <main class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" onClick=${() => setSideOpen(true)} title=${t("head.conversations")}><${Icon} name="menu" size=${20} /></button>
        <h1>${conv ? conv.title : t("head.yourSpace")}</h1>
        <div class="top-actions">
          <button class=${"icon-btn" + (handsFree ? " on" : "")} title=${t("head.handsFree")}
            onClick=${() => { const v = !handsFree; setHandsFree(v); if (v) { toast(t("head.handsFreeOn")); setVoiceTick((x) => x + 1); } else stopSpeaking(); }}>
            <${Icon} name="speaker" size=${19} /></button>
          <button class=${"icon-btn" + (liveOpen ? " on" : "")} title=${t("head.browser")} onClick=${() => { const v = !liveOpen; setLiveOpen(v); liveClosedByUser.current = !v; }}>
            <${Icon} name="globe" size=${19} /></button>
          <${ThemeToggle} dark=${dark} onTheme=${chooseTheme} />
          <${LangSelect} lang=${lang} onLang=${chooseLang} />
          <${ModelSelect} variant="pick" ...${modelProps} />
        </div>
      </header>
      <div class="content">
        <section class="chat">
          <div class="scroll" ref=${scrollRef} onScroll=${(e) => {
            const el = e.target; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
          }}>
            ${pushBanner ? html`<div class="banner"><span class="grow">${t("push.banner")}</span>
              <button class="btn small primary" onClick=${async () => { await window.elyEnablePush(); setPushBanner(false); }}>${t("push.enable")}</button>
              <button class="icon-btn" title=${t("push.later")} onClick=${() => { localStorage.setItem("ely-push-dismissed", "1"); setPushBanner(false); }}><${Icon} name="close" size=${14} /></button></div>` : null}
            ${noModel ? html`<div class="banner warn"><span class="tag">${t("noModel.tag")}</span>
              <span class="grow">${t("noModel.text")}</span>
              ${me.role === "admin" ? html`<button class="btn small" onClick=${() => setSettingsTab("modeles")}>${t("noModel.button")}</button>` : null}</div>` : null}
            ${!cur ? html`<div class="welcome">
                <div class="eyebrow">Exactly like you</div>
                <h1>${t("welcome.h1")}<br />${t("welcome.h2")}</h1>
                <p class="sub">${t("welcome.sub")}</p>
                ${composer(true)}
                <div class="cards">${CARDS.map(([icon, k]) => html`<button class="card-btn" onClick=${() => setPrefill(t(`card.${k}Prompt`))}>
                  <${Icon} name=${icon} size=${22} /><div><b>${t(`card.${k}`)}</b><span>${t(`card.${k}Sub`)}</span></div></button>`)}</div>
                <div class="welcome-foot">${t("welcome.foot")}</div>
              </div>`
              : messages ? html`<${Thread} messages=${messages} live=${state} onSend=${send} onImage=${setLightbox} onCancel=${cancel} />`
              : html`<div class="boot" style="height:50vh"><div class="spinner big"></div></div>`}
          </div>
          ${frame && !liveOpen ? html`<div class="live-mini" onClick=${() => { setLiveOpen(true); liveClosedByUser.current = false; }}>
            <img src=${"data:image/jpeg;base64," + frame.image} /><span>${t("live.badge")}</span></div>` : null}
          ${cur ? composer(false) : null}
        </section>
        ${liveOpen ? html`<${LiveBrowser} frame=${frame} conversationId=${cur} mobile=${isMobile()}
          onClose=${() => { setLiveOpen(false); liveClosedByUser.current = true; }} />` : null}
      </div>
    </main>
    ${settingsTab ? html`<${Settings} me=${me} onMe=${setMe} tab=${settingsTab} onTab=${setSettingsTab} onClose=${() => { setSettingsTab(null); loadModels(); }} pwa=${pwa}
      prefs=${{ theme, lang, setTheme: chooseTheme, setLang: chooseLang }}
      openConversation=${(id) => { loadConvs(); pick(id); }} />` : null}
    ${dialog === "install" ? html`<${InstallDialog} onClose=${() => setDialog(null)} />` : null}
    ${dialog === "extension" ? html`<${ExtensionDialog} me=${me} onClose=${() => setDialog(null)} />` : null}
    ${lightbox ? html`<div class="lightbox" onClick=${() => setLightbox(null)}><img src=${lightbox} /></div>` : null}
    <${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
