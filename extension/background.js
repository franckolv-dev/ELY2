// Ely pour Chrome : relie ce Chrome à Ely pour qu'elle agisse avec vos sessions.
// Ely ouvre ses onglets dans une fenêtre à part et les pilote par le protocole DevTools
// (chrome.debugger) : vrais clics, vraie frappe, lecture de la page, captures.
const DEFAULT_URL = "http://localhost:8000";
const IDLE_DETACH_MIN = 1.5; // sans commande, on détache : la barre « Ely débogue ce navigateur » disparaît
const RETRY_MIN_MS = 2_000;
const RETRY_MAX_MS = 20_000;

let ws = null;
let connecting = false;
let again = false;   // une demande de connexion est arrivée pendant une tentative
let generation = 0;  // change à chaque reconnexion : une tentative dépassée abandonne
let retryTimer = null;
let retryDelay = RETRY_MIN_MS;
// état affiché par la fenêtre de l'extension (codes traduits par popup.js)
let state = { status: "offline", user: "" };
let elyWindow = null;
const attached = new Set(); // simple cache : le service worker peut redémarrer, le débogueur reste attaché

// adresse saisie dans la fenêtre de l'extension, sinon celle d'où l'extension a été téléchargée (config.json)
async function elyUrl() {
  let { url } = await chrome.storage.local.get("url");
  if (!url) {
    try { url = (await (await fetch(chrome.runtime.getURL("config.json"))).json()).url; } catch { /* dossier du dépôt : pas de config */ }
  }
  return (url || DEFAULT_URL).trim().replace(/\/+$/, "");
}

function setState(status, extra = {}) {
  state = { status, user: state.user, ...extra };
  chrome.action.setBadgeText({ text: status === "connected" ? "" : "!" });
  chrome.action.setBadgeBackgroundColor({ color: "#b3452c" });
  chrome.runtime.sendMessage({ kind: "state", ...state }).catch(() => {});
}

function scheduleRetry() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, retryDelay);
  retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
}

// ---------------------------------------------------------------- connexion à Ely
async function connect() {
  if (connecting) { again = true; return; }
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  connecting = true;
  const gen = generation;
  try {
    const url = await elyUrl();
    let cookie = null;
    try { cookie = await chrome.cookies.get({ url, name: "ely_token" }); } catch (e) { setState("error", { detail: String(e) }); return; }
    if (!cookie) { setState("no_session", { url }); return; }  // le cookie apparaîtra à la connexion (voir onChanged)
    // Ely répond-il ? Sans cette vérification, chaque essai pendant un redémarrage d'Ely
    // inscrit une erreur « WebSocket … ERR_CONNECTION_REFUSED » dans chrome://extensions.
    try {
      const res = await fetch(url + "/api/setup", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
    } catch {
      if (gen === generation) { setState("offline", { url }); scheduleRetry(); }
      return;
    }
    if (gen !== generation) return;  // adresse ou session changée entre-temps : la tentative suivante s'en charge
    const sock = new WebSocket(url.replace(/^http/, "ws") + "/api/chrome/ws?token=" + encodeURIComponent(cookie.value));
    ws = sock;
    sock.onopen = () => {
      retryDelay = RETRY_MIN_MS;
      const m = chrome.runtime.getManifest();
      sock.send(JSON.stringify({ type: "hello", version: m.version, ua: navigator.userAgent, platform: navigator.platform }));
    };
    sock.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "welcome") { state.user = msg.user || ""; setState("connected"); }
      else if (msg.id) run(sock, msg);
    };
    sock.onclose = (e) => {
      if (ws !== sock) return;  // connexion déjà remplacée
      ws = null;
      if (e.code === 4001) { setState("expired", { url }); return; }
      setState("offline", { url });
      scheduleRetry();
    };
  } finally {
    connecting = false;
    if (again) { again = false; connect(); }
  }
}

function reconnect() {
  generation++;
  clearTimeout(retryTimer);
  retryDelay = RETRY_MIN_MS;
  const old = ws;
  ws = null;
  if (old) { try { old.close(); } catch { /* déjà fermée */ } }
  connect();
}

// le ping garde la connexion (et le service worker) en vie ; l'alarme le réveille s'il a été arrêté
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}'); else connect(); }, 20_000);
chrome.alarms.create("ely", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(({ name }) => { if (name === "detach") detachAll(); else connect(); });
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(() => connect());
chrome.cookies.onChanged.addListener(({ cookie }) => { if (cookie.name === "ely_token") reconnect(); });
chrome.storage.onChanged.addListener((changes) => { if (changes.url) reconnect(); });
chrome.runtime.onMessage.addListener((m, _sender, reply) => {
  if (m.kind === "get") { (async () => reply({ ...state, url: await elyUrl() }))(); return true; }
  if (m.kind === "reconnect") { reconnect(); reply(true); }
  return false;
});
connect();

// ---------------------------------------------------------------- commandes d'Ely
async function run(sock, msg) {
  let out;
  try {
    const fn = COMMANDS[msg.cmd];
    if (!fn) throw new Error(`commande inconnue : ${msg.cmd}`);
    out = { id: msg.id, ok: true, result: await fn(msg) };
  } catch (e) {
    out = { id: msg.id, ok: false, error: String((e && e.message) || e) };
  }
  if (sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(out));
  await chrome.alarms.create("detach", { delayInMinutes: IDLE_DETACH_MIN }); // remplace la précédente : le délai repart
}

async function windowExists(id) {
  try { await chrome.windows.get(id); return true; } catch { return false; }
}

async function getElyWindow() {
  if (elyWindow !== null && await windowExists(elyWindow)) return elyWindow;
  const { win } = await chrome.storage.session.get("win");
  elyWindow = win && await windowExists(win) ? win : null;
  return elyWindow;
}

// Chrome refuse de piloter un onglet où se trouve une page d'une autre extension, et lâche l'onglet dès qu'il en
// apparaît une : le menu qu'un gestionnaire de mots de passe (Passbolt…) glisse dans un champ, par exemple. Ces
// cadres n'appartiennent pas au site : avant de reprendre l'onglet, on les retire (onglets d'Ely seulement).
const FOREIGN_FRAME = /chrome-extension:\/\/ URL of different extension/i;
const PDF_VIEWER = "mhjfbmdgcfjbbpaeojofohoefgiehjai"; // lecteur PDF intégré à Chrome

// Exécutée dans chaque cadre de la page, racines fantômes fermées comprises ; rend les adresses retirées.
// `blind` : le premier passage n'a pas suffi, le cadre fautif a été chargé par script (son attribut src ne le montre
// pas, et Chrome ne le montre à aucune autre extension) : on retire aussi les cadres opaques sans adresse visible.
function removeForeignFrames(own, blind) {
  const removed = [];
  const foreign = (url) => url.startsWith("chrome-extension://") && !url.startsWith(own);
  const visit = (root) => {
    for (const el of root.querySelectorAll("*")) {
      const url = el.getAttribute("src") || el.getAttribute("data") || "";
      const frame = el instanceof HTMLIFrameElement || el instanceof HTMLFrameElement;
      const embedded = frame || el instanceof HTMLEmbedElement || el instanceof HTMLObjectElement;
      const hidden = frame && blind && (!url || url === "about:blank" || url.startsWith("javascript:")) && !el.contentDocument;
      if ((embedded && foreign(url)) || hidden) {
        removed.push(url || "?");
        el.remove(); // le vider (src = about:blank) ne suffit pas : Chrome poursuivrait son chargement
        continue;
      }
      const shadow = chrome.dom.openOrClosedShadowRoot(el);
      if (shadow) visit(shadow);
    }
  };
  visit(document);
  return removed;
}

async function clearForeignFrames(tabId, blind) {
  try {
    const frames = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, injectImmediately: true,
      func: removeForeignFrames, args: [chrome.runtime.getURL(""), blind] });
    return frames.flatMap((f) => f.result || []);
  } catch {
    return []; // PDF, page d'une autre extension… : rien à retirer
  }
}

function blockedError(urls, url) {
  const seen = urls.filter((u) => u.startsWith("chrome-extension://"));
  const ids = [...new Set(seen.map((u) => new URL(u).host))];
  const e = new Error(/\.pdf([?#]|$)/i.test(url) || (ids.length === 1 && ids[0] === PDF_VIEWER)
    ? "Chrome affiche un PDF dans cet onglet : Ely ne peut pas le piloter. Ouvrez une autre page (open), ou téléchargez le PDF pour le lire."
    : "Chrome refuse de laisser Ely piloter cet onglet : une autre extension y a placé une de ses pages ("
      + (seen.slice(0, 3).join(", ") || "sans adresse visible") + "). "
      + (ids.length ? `Dans ${ids.map((id) => `chrome://extensions/?id=${id}`).join(", ")}, réglez son « Accès aux sites » `
        + "sur « Lorsque vous cliquez sur l'extension », ou désactivez-la." : ""));
  e.blocked = true;
  return e;
}

async function attach(tabId, focus = true) {
  if (attached.has(tabId)) return;
  const foreign = new Set();
  for (let attempt = 0; ; attempt++) {
    // retirés AVANT de reprendre l'onglet : un cadre encore en chargement le ferait relâcher aussitôt après ;
    // à l'aveugle seulement si Chrome a déjà refusé une fois
    for (const url of await clearForeignFrames(tabId, attempt > 0)) foreign.add(url);
    try {
      await chrome.debugger.attach({ tabId }, "1.3");
      break;
    } catch (e) {
      const msg = String(e && e.message);
      if (/already attached/i.test(msg)) break; // déjà attaché avant un redémarrage du service worker
      if (!FOREIGN_FRAME.test(msg)) throw e;
      if (attempt === 3) throw blockedError([...foreign], (await chrome.tabs.get(tabId).catch(() => ({}))).url || "");
      await new Promise((ok) => setTimeout(ok, 150)); // un menu a pu resurgir entre-temps
    }
  }
  attached.add(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Page.enable");
  // page réputée au premier plan, même fenêtre en arrière-plan ; sauf quand Chrome vient de lâcher l'onglet à cause d'un
  // menu d'extension : le focus rendu au champ ferait resurgir le menu, et Chrome lâcherait l'onglet aussitôt
  if (focus) await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
}

// changer de page sans le débogueur : seule issue d'un onglet que Chrome interdit de piloter (PDF…)
function navigateWithoutDebugger(tabId, url) {
  return new Promise((ok) => {
    const done = () => {
      chrome.tabs.onUpdated.removeListener(listen);
      clearTimeout(timer);
      ok();
    };
    const listen = (id, info) => { if (id === tabId && info.status === "complete") done(); };
    const timer = setTimeout(done, 30_000);
    chrome.tabs.onUpdated.addListener(listen);
    chrome.tabs.update(tabId, { url }).catch(done);
  });
}

// tous les onglets attachés, y compris avant un redémarrage du service worker (cache vide) ;
// ceux qu'un autre débogueur (DevTools) tient refusent le détachement, sans conséquence
async function detachAll() {
  attached.clear();
  for (const t of await chrome.debugger.getTargets()) {
    if (t.attached && t.tabId !== undefined) chrome.debugger.detach({ tabId: t.tabId }).catch(() => {});
  }
}

chrome.debugger.onDetach.addListener((src) => attached.delete(src.tabId));
chrome.debugger.onEvent.addListener((src, method) => {
  // alertes et confirmations : acceptées, sinon la page resterait bloquée
  if (method === "Page.javascriptDialogOpening") {
    chrome.debugger.sendCommand({ tabId: src.tabId }, "Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
  }
});
chrome.windows.onRemoved.addListener((id) => { if (id === elyWindow) elyWindow = null; });

const COMMANDS = {
  // nouvel onglet dans la fenêtre d'Ely (créée au besoin, sans prendre le focus)
  async new_tab({ url }) {
    const target = url || "about:blank";
    const win = await getElyWindow();
    if (win === null) {
      const w = await chrome.windows.create({ url: target, focused: false, width: 1280, height: 900 });
      elyWindow = w.id;
      await chrome.storage.session.set({ win: w.id });
      return { tab_id: w.tabs[0].id };
    }
    const tab = await chrome.tabs.create({ windowId: win, url: target, active: true });
    return { tab_id: tab.id };
  },
  async tabs() {
    const win = await getElyWindow();
    if (win === null) return [];
    const tabs = await chrome.tabs.query({ windowId: win });
    return tabs.map((t) => ({ tab_id: t.id, url: t.url || t.pendingUrl || "", title: t.title || "", opener: t.openerTabId ?? null, active: t.active }));
  },
  async activate({ tab_id }) {
    await chrome.tabs.update(tab_id, { active: true });
    return true;
  },
  async close({ tab_id }) {
    attached.delete(tab_id);
    await chrome.tabs.remove(tab_id);
    return true;
  },
  async cdp({ tab_id, method, params }) {
    // onglet bloqué (PDF, cadre d'extension tenace) : Ely n'en est pas prisonnière, changer de page ne demande pas
    // la liaison avec l'onglet
    const navigate = method === "Page.navigate";
    const leave = async () => {
      await navigateWithoutDebugger(tab_id, params.url);
      attached.delete(tab_id);
      await attach(tab_id);
      return {};
    };
    try {
      await attach(tab_id);
    } catch (e) {
      if (e.blocked && navigate) return await leave();
      throw e;
    }
    const send = () => chrome.debugger.sendCommand({ tabId: tab_id }, method, params || {});
    try {
      return await send();
    } catch (e) {
      const msg = String(e && e.message);
      const uncertain = /Detached while handling command/i.test(msg) || FOREIGN_FRAME.test(msg);
      if (!uncertain && !/is not attached/i.test(msg)) throw e;
      if (navigate) return await leave();
      // Chrome a lâché l'onglet : un cadre d'une autre extension y est apparu (voir removeForeignFrames). On le reprend.
      attached.delete(tab_id);
      await attach(tab_id, false);
      if (!uncertain) return await send(); // lâché avant la commande : elle n'était pas partie
      // issue incertaine, jamais renvoyée. Un clic ou une frappe est arrivé (c'est lui qui fait surgir le menu d'un
      // champ) ; toute autre commande échoue, et Ely relit la page.
      if (method.startsWith("Input.")) return {};
      throw new Error("Chrome a coupé la liaison pendant la commande (une autre extension a glissé un cadre dans la page) : "
        + "relisez la page avant de recommencer.");
    }
  },
};
