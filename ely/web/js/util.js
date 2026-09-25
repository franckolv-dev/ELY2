// Utilitaires partagés : rendu markdown, icônes, dates, notifications éphémères.
import { html } from "/static/vendor/preact-htm.js";
import { marked } from "/static/vendor/marked.js";
import DOMPurify from "/static/vendor/purify.js";

marked.setOptions({ gfm: true, breaks: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    const href = node.getAttribute("href") || "";
    if (!href.startsWith("/")) { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
  }
});

export function md(text) {
  return DOMPurify.sanitize(marked.parse(text || ""));
}

// Icônes au trait fin (grille 16 px), comme dans la maquette
const P = {
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  plus: "M8 2.5v11M2.5 8h11",
  send: "M8 13V3M3.5 7.5L8 3l4.5 4.5",
  stop: "M5 5h6v6H5z",
  mic: "M6.5 1.5h3a1 1 0 0 1 1 1v5a2.5 2.5 0 0 1-5 0v-5a1 1 0 0 1 1-1zM3 7.5a5 5 0 0 0 10 0M8 12.5v2",
  search: "M6.5 2.2a4.3 4.3 0 1 0 0 8.6 4.3 4.3 0 0 0 0-8.6zM9.8 9.8l3.7 3.7",
  gear: "M8 5.8a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  globe: "M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM1.5 8h13M8 1.5c1.8 1.8 2.7 4 2.7 6.5S9.8 12.7 8 14.5M8 1.5C6.2 3.3 5.3 5.5 5.3 8s.9 4.7 2.7 6.5",
  close: "M3.5 3.5l9 9M12.5 3.5l-9 9",
  chev: "M4 6.5l4 4 4-4",
  dots: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  trash: "M2.5 4h11M6.5 4V2.5h3V4M4 4l.7 9.5h6.6L12 4",
  pin: "M8 11v3.5M5.5 2h5l-.7 4.5 2.2 2.5H4l2.2-2.5z",
  edit: "M2.5 13.5h3l7.5-7.5-3-3-7.5 7.5zM9 4l3 3",
  refresh: "M13.5 7.5a5.5 5.5 0 1 0-1.6 4M13.5 2.5v5h-5",
  back: "M10 3.5L5.5 8l4.5 4.5",
  speaker: "M7.5 3.5L4.5 6H2v4h2.5l3 2.5zM10.5 6a2.8 2.8 0 0 1 0 4M12.5 4a5.6 5.6 0 0 1 0 8",
  logout: "M6 13.5H3.5a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1H6M10.5 11l3-3-3-3M13.5 8H6",
  download: "M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10",
  check: "M3 8.4l3.2 3.1L13 4.5",
  sun: "M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9L13 13M3 13l1.1-1.1M11.9 4.1L13 3",
  moon: "M13.5 9.8A6 6 0 0 1 6.2 2.5a6 6 0 1 0 7.3 7.3z",
  phone: "M5 1.5h6a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zM7 12.5h2",
};

export const Icon = ({ name, size = 16, stroke = 1.5 }) => html`
  <svg viewBox="0 0 16 16" width=${size} height=${size} fill="none" stroke="currentColor" stroke-width=${stroke}
       stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d=${P[name] || ""} /></svg>`;

export function timeAgo(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "à l'instant";
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

export function dateTime(ts) {
  return new Date(ts * 1000).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });
}

export function groupLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const days = Math.floor((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (days <= 0) return "Aujourd'hui";
  if (days === 1) return "Hier";
  if (days < 7) return "7 derniers jours";
  if (days < 31) return "Ce mois-ci";
  return "Plus ancien";
}

// Étiquette d'extension (PDF, DOCX…) plutôt qu'un émoji
export function fileExt(path) {
  const name = (path || "").split("/").pop();
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  return ext && ext.length <= 5 ? ext : "fichier";
}

export const FileTag = ({ path }) => html`<span class="ext">${fileExt(path)}</span>`;

export function clock(ts) {
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

export function bytes(n) {
  if (n < 1024) return `${n} o`;
  if (n < 1048576) return `${Math.round(n / 1024)} Ko`;
  return `${(n / 1048576).toFixed(1)} Mo`;
}

const toastListeners = new Set();
export function toast(text, ms = 3200) {
  const t = { id: Math.random(), text };
  toastListeners.forEach((fn) => fn(t, ms));
}
export function onToast(fn) { toastListeners.add(fn); return () => toastListeners.delete(fn); }

export const isMobile = () => matchMedia("(max-width: 860px)").matches;
export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const isImage = (p) => /\.(png|jpe?g|gif|webp)$/i.test(p || "");

// ---------------------------------------------------------------- thème et accent (préférences de cet appareil)
const THEME_BG = { light: "#f5f7fa", dark: "#2a2e32" };
export const ACCENTS = [["lime", "Lime", "oklch(0.9 0.19 122)"], ["glacier", "Glacier", "oklch(0.86 0.1 250)"], ["signal", "Signal", "oklch(0.78 0.17 52)"]];

function pref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch { /* navigation privée */ }
}

export const storedTheme = () => pref("ely-theme", "auto");
export const storedAccent = () => pref("ely-accent", "lime");
export const prefersDark = () => matchMedia("(prefers-color-scheme: dark)").matches;

export function applyTheme(v, save = true) {
  const root = document.documentElement;
  if (v === "light" || v === "dark") root.setAttribute("data-theme", v); else root.removeAttribute("data-theme");
  for (const m of document.querySelectorAll('meta[name="theme-color"]')) {
    const own = m.media.includes("dark") ? "dark" : "light";
    m.setAttribute("content", THEME_BG[v === "light" || v === "dark" ? v : own]);
  }
  if (save) savePref("ely-theme", v);
}

export function applyAccent(v, save = true) {
  if (v && v !== "lime") document.documentElement.setAttribute("data-accent", v); else document.documentElement.removeAttribute("data-accent");
  if (save) savePref("ely-accent", v);
}
