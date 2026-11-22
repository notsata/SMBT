"use client";

// Stand-in for next/link + next/navigation in the single-page snapshot build. The artifact host only
// passes a plain "#name" hash, so each route maps to a name and query strings stay in memory.
import { type AnchorHTMLAttributes, type MouseEvent, type ReactNode, useMemo, useSyncExternalStore } from "react";

const NAMES: Record<string, string> = {
  "/": "",
  "/scanner/": "scanner",
  "/analyze/": "analyze",
  "/alerts/": "alerts",
  "/validate/": "validate",
  "/history/": "history",
  "/charts/": "charts",
  "/analyst/": "analyst",
  "/settings/": "data", // "settings" is reserved by the host
  "/login/": "login",
};
const PATHS: Record<string, string> = Object.fromEntries(Object.entries(NAMES).map(([p, n]) => [n, p]));

type Loc = { path: string; search: string };
const lastSearch: Record<string, string> = {};
const subs = new Set<() => void>();

function normalize(p: string): string {
  if (p === "" || p === "/") return "/";
  const withSlash = p.endsWith("/") ? p : p + "/";
  return NAMES[withSlash] !== undefined ? withSlash : "/";
}

function fromHash(): Loc {
  const path = PATHS[location.hash.replace(/^#/, "")] ?? "/";
  return { path, search: lastSearch[path] ?? "" };
}

let loc: Loc = fromHash();
const emit = () => subs.forEach((f) => f());

window.addEventListener("hashchange", () => {
  const next = fromHash();
  if (next.path !== loc.path) {
    loc = next;
    emit();
  }
});

export function navigate(href: string) {
  const [p, q = ""] = href.split("?");
  const path = normalize(p);
  lastSearch[path] = q;
  loc = { path, search: q };
  const name = NAMES[path];
  if (location.hash.replace(/^#/, "") !== name) location.hash = name;
  emit();
  window.scrollTo(0, 0);
}

function useLoc(): Loc {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
    () => loc,
  );
}

export function usePathname(): string {
  return useLoc().path;
}

export function useSearchParams(): URLSearchParams {
  const { search } = useLoc();
  return useMemo(() => new URLSearchParams(search), [search]);
}

const router = { push: navigate, replace: navigate, back: () => history.back(), forward: () => history.forward(), refresh: () => undefined, prefetch: () => undefined };

export function useRouter() {
  return router;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { href: string; children?: ReactNode };

export default function Link({ href, children, onClick, ...rest }: LinkProps) {
  const name = NAMES[normalize(href.split("?")[0])];
  return (
    <a
      href={`#${name}`}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(e);
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(href);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
