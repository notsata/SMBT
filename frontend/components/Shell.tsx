"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { onOfflineData } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard", key: "d", icon: "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10" },
  { href: "/scanner/", label: "Scanner", key: "s", icon: "M11 4a7 7 0 100 14 7 7 0 000-14zm9 16l-4.3-4.3" },
  { href: "/analyze/", label: "Analyze", key: "a", icon: "M4 19V9m6 10V5m6 14v-7m4 7H2" },
  { href: "/alerts/", label: "Alerts", key: "l", icon: "M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0a3 3 0 11-6 0" },
  { href: "/validate/", label: "Validate", key: "v", icon: "M9 12l2 2 4-4M7 4h10l3 3v13H4V4h3" },
  { href: "/history/", label: "History", key: "h", icon: "M12 8v4l3 2m6-2a9 9 0 11-3-6.7L21 3v5h-5" },
  { href: "/charts/", label: "Charts", key: "c", icon: "M3 17l5-5 4 4 8-9" },
  { href: "/analyst/", label: "AI Analyst", key: "i", icon: "M8 10h8M8 14h5m-9 6l2-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-7l-4 4" },
  { href: "/settings/", label: "Settings & Data", key: "t", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zm7.4-3a7.4 7.4 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 00-2-1.2L14.5 3h-5l-.4 2.6a7 7 0 00-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 000 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 002 1.2l.4 2.6h5l.4-2.6a7 7 0 002-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z" },
];
const MOBILE = ["/", "/scanner/", "/analyze/", "/alerts/"];

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function isActive(path: string, href: string) {
  return href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, ""));
}

export function applyTheme(t: string) {
  const dark = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  try {
    localStorage.setItem("smbt-theme", t);
  } catch {
    /* storage unavailable (private mode) */
  }
  window.dispatchEvent(new Event("smbt-theme"));
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname() || "/";
  const router = useRouter();
  const [more, setMore] = useState(false);
  const [help, setHelp] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
    setOnline(navigator.onLine);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    const unsub = onOfflineData(setCachedAt);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      unsub();
    };
  }, []);

  useEffect(() => {
    setMore(false);
  }, [path]);

  useEffect(() => {
    let g = false;
    let timer: ReturnType<typeof setTimeout>;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable=true]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") {
        e.preventDefault();
        search.current?.focus();
      } else if (e.key === "?") setHelp((h) => !h);
      else if (e.key === "Escape") setHelp(false);
      else if (e.key === "f") window.dispatchEvent(new Event("smbt-fullscreen"));
      else if (e.key === "g") {
        g = true;
        clearTimeout(timer);
        timer = setTimeout(() => (g = false), 1200);
      } else if (g) {
        const n = NAV.find((x) => x.key === e.key);
        if (n) router.push(n.href);
        g = false;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  const go = (e: React.FormEvent) => {
    e.preventDefault();
    const v = search.current?.value.trim().toUpperCase();
    if (v) router.push(`/analyze/?s=${encodeURIComponent(v)}`);
    search.current?.blur();
  };

  return (
    <div className="min-h-dvh lg:flex">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-line bg-panel lg:flex">
        <div className="px-4 py-4">
          <div className="text-sm font-bold tracking-wide">SMBT</div>
          <div className="text-[11px] text-muted">Turning-point research terminal</div>
        </div>
        <nav className="flex-1 space-y-0.5 px-2" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm ${isActive(path, n.href) ? "bg-panel2 text-fg" : "text-muted hover:bg-panel2 hover:text-fg"}`}>
              <Icon d={n.icon} />
              <span className="flex-1">{n.label}</span>
              <kbd className="text-[10px] text-muted/70">g {n.key}</kbd>
            </Link>
          ))}
        </nav>
        <button onClick={() => setHelp(true)} className="m-3 rounded-lg border border-line px-3 py-1.5 text-xs text-muted hover:text-fg">
          Keyboard shortcuts <kbd>?</kbd>
        </button>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 border-b border-line bg-bg/90 pt-[env(safe-area-inset-top)] backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-2">
            <span className="text-sm font-bold lg:hidden">SMBT</span>
            <form onSubmit={go} className="flex-1" role="search">
              <input
                ref={search}
                aria-label="Search ticker"
                placeholder="Ticker (e.g. NVDA)  /"
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
                className="w-full max-w-sm rounded-lg border border-line bg-panel px-3 py-1.5 text-sm placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </form>
            {!online && <span className="rounded bg-warn/15 px-2 py-0.5 text-[11px] text-warn">Offline</span>}
          </div>
          {cachedAt && (
            <div className="bg-warn/15 px-4 py-1 text-[11px] text-warn" role="status">
              Showing cached data saved {new Date(cachedAt).toLocaleString()} — network unavailable.
            </div>
          )}
        </header>
        <main className="pb-safe mx-auto max-w-[1600px] px-3 py-4 sm:px-4">{children}</main>
      </div>

      <nav aria-label="Main mobile" className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        <div className="grid grid-cols-5">
          {NAV.filter((n) => MOBILE.includes(n.href)).map((n) => (
            <Link key={n.href} href={n.href} className={`flex flex-col items-center gap-0.5 py-2 text-[10px] ${isActive(path, n.href) ? "text-accent" : "text-muted"}`}>
              <Icon d={n.icon} />
              {n.label}
            </Link>
          ))}
          <button onClick={() => setMore(!more)} aria-expanded={more} className={`flex flex-col items-center gap-0.5 py-2 text-[10px] ${more ? "text-accent" : "text-muted"}`}>
            <Icon d="M4 6h16M4 12h16M4 18h16" />
            More
          </button>
        </div>
      </nav>
      {more && (
        <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setMore(false)}>
          <div className="absolute inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+3.6rem)] rounded-t-2xl border-t border-line bg-panel p-2" onClick={(e) => e.stopPropagation()}>
            {NAV.filter((n) => !MOBILE.includes(n.href)).map((n) => (
              <Link key={n.href} href={n.href} className="flex items-center gap-3 rounded-lg px-4 py-3 text-sm hover:bg-panel2">
                <Icon d={n.icon} />
                {n.label}
              </Link>
            ))}
          </div>
        </div>
      )}
      {help && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onClick={() => setHelp(false)} role="dialog" aria-label="Keyboard shortcuts">
          <div className="w-full max-w-sm rounded-xl border border-line bg-panel p-4 text-sm" onClick={(e) => e.stopPropagation()}>
            <h2 className="mb-3 font-semibold">Keyboard shortcuts</h2>
            <ul className="space-y-1.5">
              <li><kbd>/</kbd> search ticker</li>
              <li><kbd>f</kbd> full-screen chart</li>
              {NAV.map((n) => (
                <li key={n.key}>
                  <kbd>g</kbd> <kbd>{n.key}</kbd> {n.label}
                </li>
              ))}
              <li><kbd>?</kbd> toggle this help · <kbd>Esc</kbd> close</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
