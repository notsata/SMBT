// SMBT service worker: app-shell caching, offline access to previously loaded data, Web Push.
const VERSION = "smbt-v1";
const SHELL = `${VERSION}-shell`;
const DATA = `${VERSION}-data`;
const PAGES = ["/", "/scanner/", "/analyze/", "/alerts/", "/validate/", "/history/", "/charts/", "/analyst/", "/settings/", "/login/", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => Promise.allSettled(PAGES.map((p) => c.add(p)))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

async function networkFirstApi(req) {
  const cache = await caches.open(DATA);
  try {
    const res = await fetch(req);
    if (res.ok) {
      // Store a copy stamped with when it was saved, so the UI can label offline data honestly.
      const body = await res.clone().blob();
      const headers = new Headers(res.headers);
      headers.set("x-smbt-cached-at", new Date().toISOString());
      await cache.put(req, new Response(body, { status: res.status, statusText: res.statusText, headers }));
    }
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    return new Response(JSON.stringify({ detail: "Offline and no cached copy of this data yet." }), { status: 503, headers: { "Content-Type": "application/json" } });
  }
}

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
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/auth") || url.pathname.startsWith("/api/admin")) return;
  if (url.pathname.startsWith("/api/")) return e.respondWith(networkFirstApi(req));
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) return e.respondWith(cacheFirst(req));
  if (req.mode === "navigate") return e.respondWith(networkFirstPage(req));
});

self.addEventListener("push", (e) => {
  let d = {};
  try {
    d = e.data ? e.data.json() : {};
  } catch (err) {
    d = { title: "SMBT alert", body: e.data ? e.data.text() : "" };
  }
  e.waitUntil(
    self.registration.showNotification(d.title || "SMBT alert", {
      body: d.body || "",
      tag: d.tag,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: d.url || "/alerts/" },
    }),
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
