// Ely pour Chrome : relie ce Chrome à Ely pour qu'elle agisse avec tes sessions.
// Ely ouvre ses onglets dans une fenêtre à part et les pilote par le protocole DevTools
// (chrome.debugger) : vrais clics, vraie frappe, lecture de la page, captures.
const DEFAULT_URL = "http://localhost:8000";
const IDLE_DETACH_MS = 90_000; // sans commande, on détache : la barre « Ely débogue ce navigateur » disparaît

let ws = null;
let state = { status: "déconnecté", detail: "", user: "" };
let elyWindow = null;
const attached = new Set();
let idleTimer = null;

async function elyUrl() {
  const { url } = await chrome.storage.local.get("url");
  return (url || DEFAULT_URL).trim().replace(/\/+$/, "");
}

function setState(status, detail = "", user = state.user) {
  state = { status, detail, user };
  chrome.action.setBadgeText({ text: status === "connecté" ? "" : "!" });
  chrome.action.setBadgeBackgroundColor({ color: "#c2410c" });
  chrome.runtime.sendMessage({ kind: "state", ...state }).catch(() => {});
}

// ---------------------------------------------------------------- connexion à Ely
async function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;
  const url = await elyUrl();
  let cookie = null;
  try { cookie = await chrome.cookies.get({ url, name: "ely_token" }); } catch (e) { setState("erreur", String(e)); return; }
  if (!cookie) { setState("non connecté", `Ouvre ${url} dans ce Chrome et connecte-toi à Ely.`); return; }
  const sock = new WebSocket(url.replace(/^http/, "ws") + "/api/chrome/ws?token=" + encodeURIComponent(cookie.value));
  ws = sock;
  sock.onopen = () => {
    const m = chrome.runtime.getManifest();
    sock.send(JSON.stringify({ type: "hello", version: m.version, ua: navigator.userAgent, platform: navigator.platform }));
  };
  sock.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "welcome") setState("connecté", "", msg.user || "");
    else if (msg.id) run(sock, msg);
  };
  sock.onclose = (e) => {
    if (ws === sock) ws = null;
    if (e.code === 4001) setState("non connecté", "Session Ely expirée : reconnecte-toi à Ely dans ce Chrome.");
    else setState("déconnecté", `Ely injoignable à ${url}. Nouvel essai dans quelques secondes.`);
  };
}

function reconnect() {
  if (ws) { try { ws.close(); } catch { /* déjà fermée */ } }
  ws = null;
  connect();
}

// le ping garde la connexion (et le service worker) en vie ; l'alarme le réveille s'il a été arrêté
setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}'); else connect(); }, 20_000);
chrome.alarms.create("ely", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.cookies.onChanged.addListener(({ cookie }) => { if (cookie.name === "ely_token") reconnect(); });
chrome.storage.onChanged.addListener((changes) => { if (changes.url) reconnect(); });
chrome.runtime.onMessage.addListener((m, _sender, reply) => {
  if (m.kind === "get") elyUrl().then((url) => reply({ ...state, url }));
  if (m.kind === "reconnect") { reconnect(); reply(true); }
  return true;
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
  clearTimeout(idleTimer);
  idleTimer = setTimeout(detachAll, IDLE_DETACH_MS);
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

async function attach(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (e) {
    if (!/already attached/i.test(String(e && e.message))) throw e; // déjà attaché avant un redémarrage du service worker
  }
  attached.add(tabId);
  await chrome.debugger.sendCommand({ tabId }, "Page.enable");
  await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
}

function detachAll() {
  for (const tabId of attached) chrome.debugger.detach({ tabId }).catch(() => {});
  attached.clear();
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
    await attach(tab_id);
    return await chrome.debugger.sendCommand({ tabId: tab_id }, method, params || {});
  },
};
