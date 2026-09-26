// Client de l'API d'Ely + flux d'événements temps réel (SSE, reconnexion automatique).
import { serverText } from "/static/js/i18n.js";

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof data === "object" ? (data.detail?.[0]?.msg || data.detail || JSON.stringify(data)) : data;
    throw new ApiError(res.status, serverText(msg));
  }
  return data;
}

export const get = (p) => api(p);
export const post = (p, body) => api(p, { method: "POST", body: body ?? {} });
export const put = (p, body) => api(p, { method: "PUT", body });
export const patch = (p, body) => api(p, { method: "PATCH", body });
export const del = (p) => api(p, { method: "DELETE" });

export async function upload(file) {
  const form = new FormData();
  form.append("file", file, file.name || "fichier");
  return api("/api/files/upload", { method: "POST", form });
}

export function connectEvents(onEvent, onState) {
  let es = null, timer = null, retry = 1000, closed = false;
  const open = () => {
    clearTimeout(timer);
    if (es) es.close();
    const src = new EventSource("/api/events");
    es = src;
    src.onopen = () => { retry = 1000; onState?.("open"); };
    src.onmessage = (e) => { if (es !== src) return; try { onEvent(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    src.onerror = () => {
      src.close();
      if (es !== src || closed) return;  // instance déjà remplacée
      onState?.("closed");
      timer = setTimeout(open, retry = Math.min(retry * 2, 15000));
    };
  };
  open();
  const onVisible = () => {
    if (document.visibilityState === "visible" && !closed && (!es || es.readyState === EventSource.CLOSED)) open();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => { closed = true; clearTimeout(timer); es?.close(); document.removeEventListener("visibilitychange", onVisible); };
}
