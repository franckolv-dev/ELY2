// Fenêtre de l'icône : état de la liaison avec Ely et adresse du serveur.
const $ = (id) => document.getElementById(id);

function show(s) {
  const on = s.status === "connecté";
  $("dot").className = "dot" + (on ? " on" : "");
  $("status").textContent = on ? `Connecté à Ely${s.user ? " (" + s.user + ")" : ""}` : s.status.charAt(0).toUpperCase() + s.status.slice(1);
  $("detail").textContent = on ? "Ely peut utiliser ce Chrome et tes sessions." : s.detail;
  if (s.url !== undefined) {
    if (document.activeElement !== $("url")) $("url").value = s.url;
    $("open").href = s.url;
  }
}

chrome.runtime.sendMessage({ kind: "get" }, show);
chrome.runtime.onMessage.addListener((m) => { if (m.kind === "state") show(m); });

$("save").addEventListener("click", async () => {
  const url = $("url").value.trim() || "http://localhost:8000";
  const { url: old } = await chrome.storage.local.get("url");
  if (url !== old) await chrome.storage.local.set({ url }); // le changement relance la connexion
  else await chrome.runtime.sendMessage({ kind: "reconnect" });
  $("open").href = url;
});
