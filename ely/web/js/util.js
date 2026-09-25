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

const P = {
  menu: "M4 6h16M4 12h16M4 18h16",
  plus: "M12 5v14M5 12h14",
  send: "M5 12h14M13 6l6 6-6 6",
  stop: "M7 7h10v10H7z",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  clip: "M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.9-7.9",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
  close: "M6 6l12 12M18 6L6 18",
  chev: "M6 9l6 6 6-6",
  dots: "M5 12h.01M12 12h.01M19 12h.01",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  pin: "M12 17v5M8 3h8l-1 7 3 3H6l3-3z",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  back: "M15 18l-6-6 6-6",
  hand: "M8 13V5a1.5 1.5 0 0 1 3 0v6m0-1V4a1.5 1.5 0 0 1 3 0v6m0-1V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-2.7L3.5 14a1.5 1.5 0 0 1 2.4-1.8L8 14",
  speaker: "M11 5L6 9H3v6h3l5 4zM16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  download: "M12 3v12M7 10l5 5 5-5M5 21h14",
};

export const Icon = ({ name, size = 20 }) => html`
  <svg viewBox="0 0 24 24" width=${size} height=${size} fill="none" stroke="currentColor" stroke-width="1.9"
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

export function fileIcon(path) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼️";
  if (ext === "pdf") return "📕";
  if (["doc", "docx", "odt"].includes(ext)) return "📘";
  if (["xls", "xlsx", "csv", "ods"].includes(ext)) return "📗";
  if (["ppt", "pptx"].includes(ext)) return "📙";
  if (["mp3", "wav", "m4a", "ogg"].includes(ext)) return "🎵";
  if (["zip", "gz", "tar"].includes(ext)) return "🗜️";
  return "📄";
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
export const isImage = (p) => /\.(png|jpe?g|gif|webp)$/i.test(p || "");
