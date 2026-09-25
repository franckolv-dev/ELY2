// Service worker d'Ely : démarrage instantané (coquille en cache) et notifications push.
const CACHE = "ely-v3";
const SHELL = ["/", "/static/style.css", "/static/js/app.js", "/static/js/api.js", "/static/js/chat.js",
  "/static/js/settings.js", "/static/js/util.js", "/static/js/voice.js", "/static/vendor/preact-htm.js",
  "/static/vendor/marked.js", "/static/vendor/purify.js", "/static/icons/icon.svg", "/static/icons/icon-192.png",
  "/static/fonts/geist.woff2", "/static/fonts/geist-mono.woff2", "/static/fonts/instrument-serif.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/files/") || url.pathname.startsWith("/ics/")) return;
  // réseau d'abord (toujours la dernière version), cache si hors ligne
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data.json(); } catch { data = { title: "Ely", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(data.title || "Ely", {
    body: data.body || "", tag: data.tag || undefined, renotify: !!data.tag, data: { url: data.url || "/" },
    icon: "/static/icons/icon-192.png", badge: "/static/icons/badge-96.png", vibrate: [80, 40, 80],
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "/", location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if (c.url.startsWith(location.origin)) { c.postMessage({ type: "open", url: target }); return c.focus(); }
    }
    return self.clients.openWindow(target);
  }));
});
