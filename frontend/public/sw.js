// SMBT service worker: caches only the static app shell (code, styles, icons) so the installed app opens fast.
// It never caches /api responses or WebSocket data, so no market or user information is kept on the device.
const VERSION = "smbt-v2";
const SHELL = `${VERSION}-shell`;
const PAGES = ["/", "/scanner/", "/analyze/", "/alerts/", "/validate/", "/history/", "/charts/", "/analyst/", "/settings/", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => Promise.allSettled(PAGES.map((p) => c.add(p)))).then(() => self.skipWaiting()));
});

// Deletes every older cache, including v1's cached API data from earlier releases.
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

async function cacheFirst(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

async function networkFirstPage(req) {
  const cache = await caches.open(SHELL);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    return (await cache.match(req, { ignoreSearch: true })) || (await cache.match("/")) || Response.error();
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/") || url.pathname === "/ws") return;
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) return e.respondWith(cacheFirst(req));
  if (req.mode === "navigate") return e.respondWith(networkFirstPage(req));
});

// Local alert notifications (shown by the open page) focus the app when tapped. There is no push handler.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) if ("focus" in c) return c.focus();
      return self.clients.openWindow("/alerts/");
    }),
  );
});
