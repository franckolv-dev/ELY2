// Utilitaires partagés : rendu markdown, icônes, dates, notifications éphémères.
import { html } from "/static/vendor/preact-htm.js";
import { marked } from "/static/vendor/marked.js";
import DOMPurify from "/static/vendor/purify.js";
import { locale, t } from "/static/js/i18n.js";

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
  install: "M8 1.5v7.5M5 6l3 3 3-3M2.5 10.5v2a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-2",
  check: "M3 8.4l3.2 3.1L13 4.5",
  sun: "M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9L13 13M3 13l1.1-1.1M11.9 4.1L13 3",
  moon: "M13.5 9.8A6 6 0 0 1 6.2 2.5a6 6 0 1 0 7.3 7.3z",
  phone: "M5 1.5h6a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1zM7 12.5h2",
};

// Icônes plus grandes (grille 24 px, dessin façon Feather) pour la barre latérale, l'accueil et l'en-tête
const P24 = {
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  calendar: "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01",
  mail: "M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM22 7l-10 7L2 7",
  doc: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
  tasks: "M3 4h5v5H3zM3 16l2 2 4-4M12 6h9M12 11h9M12 17h9",
  arrowRight: "M5 12h14M13 6l6 6-6 6",
  arrowUp: "M12 19V5M5 12l7-7 7 7",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  mic: "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3",
  plus: "M12 5v14M5 12h14",
  sun: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  speaker: "M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14",
  globe: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
  menu: "M3 6h18M3 12h18M3 18h18",
  puzzle: "M4.5 7.5h4a2.2 2.2 0 1 1 4 0h4v4a2.2 2.2 0 1 1 0 4v4h-12z",
  phone: "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2",
};

export const Icon = ({ name, size = 16, stroke }) => {
  const big = name in P24;
  return html`<svg viewBox=${big ? "0 0 24 24" : "0 0 16 16"} width=${size} height=${size} fill="none" stroke="currentColor"
    stroke-width=${stroke ?? (big ? 1.7 : 1.5)} stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d=${big ? P24[name] : P[name] || ""} /></svg>`;
};

// Logo d'Ely : quatre pétales
const PETALS = "M5.6 1H6.45A4.6 4.6 0 0 1 11.05 5.6V9.95A1.1 1.1 0 0 1 9.95 11.05H5.6A4.6 4.6 0 0 1 1 6.45V5.6A4.6 4.6 0 0 1 5.6 1ZM17.55 1H18.4A4.6 4.6 0 0 1 23 5.6V6.45A4.6 4.6 0 0 1 18.4 11.05H14.05A1.1 1.1 0 0 1 12.95 9.95V5.6A4.6 4.6 0 0 1 17.55 1ZM5.6 12.95H9.95A1.1 1.1 0 0 1 11.05 14.05V18.4A4.6 4.6 0 0 1 6.45 23H5.6A4.6 4.6 0 0 1 1 18.4V17.55A4.6 4.6 0 0 1 5.6 12.95ZM14.05 12.95H18.4A4.6 4.6 0 0 1 23 17.55V18.4A4.6 4.6 0 0 1 18.4 23H17.55A4.6 4.6 0 0 1 12.95 18.4V14.05A1.1 1.1 0 0 1 14.05 12.95Z";
export const Logo = ({ size = 26, word = true }) => html`<span class="logo">
  <svg class="logo-mark" viewBox="0 0 24 24" width=${size} height=${size} aria-hidden="true"><path d=${PETALS} fill="currentColor" /></svg>
  ${word ? html`<span class="logo-word">ELY</span>` : null}</span>`;

export function timeAgo(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return t("date.now");
  if (diff < 3600) return t("date.minAgo", { n: Math.floor(diff / 60) });
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(locale(), { day: "numeric", month: "short" });
}

export function dateTime(ts) {
  return new Date(ts * 1000).toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" });
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
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(locale(), { day: "numeric", month: "short" });
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

// ---------------------------------------------------------------- thème (préférence de cet appareil)
const THEME_BG = { light: "#ffffff", dark: "#14221d" };

function pref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch { /* navigation privée */ }
}

export const storedTheme = () => pref("ely-theme", "auto");
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
