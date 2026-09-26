// Fenêtre de l'icône : état de la liaison avec Ely et adresse du serveur (français ou anglais selon Chrome).
const $ = (id) => document.getElementById(id);
const EN = !(navigator.language || "fr").toLowerCase().startsWith("fr");
const TXT = EN ? {
  connected: (s) => `Connected to Ely${s.user ? " (" + s.user + ")" : ""}`, connectedDetail: "Ely can use this Chrome and your sessions.",
  offline: "Disconnected", offlineDetail: (s) => `Ely can't be reached at ${s.url}. Retrying in a few seconds.`,
  no_session: "Not signed in", no_sessionDetail: (s) => `Open ${s.url} in this Chrome and sign in to Ely.`,
  expired: "Session expired", expiredDetail: () => "Sign in to Ely again in this Chrome.",
  error: "Error", errorDetail: (s) => s.detail || "",
  address: "Ely address", reconnect: "Reconnect", open: "Open Ely",
} : {
  connected: (s) => `Connecté à Ely${s.user ? " (" + s.user + ")" : ""}`, connectedDetail: "Ely peut utiliser ce Chrome et vos sessions.",
  offline: "Déconnecté", offlineDetail: (s) => `Ely est injoignable à ${s.url}. Nouvel essai dans quelques secondes.`,
  no_session: "Non connecté", no_sessionDetail: (s) => `Ouvrez ${s.url} dans ce Chrome et connectez-vous à Ely.`,
  expired: "Session expirée", expiredDetail: () => "Reconnectez-vous à Ely dans ce Chrome.",
  error: "Erreur", errorDetail: (s) => s.detail || "",
  address: "Adresse d'Ely", reconnect: "Reconnecter", open: "Ouvrir Ely",
};

document.documentElement.lang = EN ? "en" : "fr";
$("addr").textContent = TXT.address;
$("save").textContent = TXT.reconnect;
$("open").textContent = TXT.open;

function show(s) {
  const on = s.status === "connected";
  $("dot").className = "dot" + (on ? " on" : "");
  $("status").textContent = on ? TXT.connected(s) : (TXT[s.status] || s.status);
  $("detail").textContent = on ? TXT.connectedDetail : (TXT[s.status + "Detail"]?.(s) || "");
  if (s.url !== undefined) {
    if (document.activeElement !== $("url")) $("url").value = s.url;
    $("open").href = s.url;
  }
}

chrome.runtime.sendMessage({ kind: "get" }, show);
chrome.runtime.onMessage.addListener((m) => { if (m.kind === "state") show(m); });

$("save").addEventListener("click", async () => {
  const url = $("url").value.trim(); // vide : adresse d'origine (celle du téléchargement, sinon localhost)
  const { url: old } = await chrome.storage.local.get("url");
  if (url !== (old || "")) await (url ? chrome.storage.local.set({ url }) : chrome.storage.local.remove("url")); // relance la connexion
  else await chrome.runtime.sendMessage({ kind: "reconnect" });
  if (url) $("open").href = url;
});
