"use client";

// One shared WebSocket to the server for every component. Reconnects with backoff, resubscribes after a drop
// (and immediately when a phone wakes the page), and keeps nothing once the tab closes.
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AlertEvent, AlertRule, LiveScores, MinuteBar, Quote, SessionInfo } from "./api";

type Msg = { type: string; symbol?: string; [k: string]: unknown };
type ConnState = "connecting" | "open" | "closed";

class LiveClient {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private counts = new Map<string, number>();
  private listeners = new Set<(m: Msg) => void>();
  private stateSubs = new Set<() => void>();
  rules: AlertRule[] = [];
  alertEvents: AlertEvent[] = [];
  alertError: string | null = null;
  state: ConnState = "closed";
  hello: { session: SessionInfo; licensing: { access_mode: string }; max_symbols: number } | null = null;
  quotes = new Map<string, Quote>();

  private setState(s: ConnState) {
    this.state = s;
    this.stateSubs.forEach((f) => f());
  }

  connect() {
    if (typeof window === "undefined" || (this.ws && this.ws.readyState <= 1)) return;
    if (this.timer) clearTimeout(this.timer);
    this.setState("connecting");
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.attempts = 0;
      this.setState("open");
      if (this.counts.size) this.send({ op: "subscribe", symbols: [...this.counts.keys()] });
      if (this.rules.length) this.send({ op: "alerts", rules: this.rules });
    };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data) as Msg;
      const items = m.type === "batch" ? (m.items as Msg[]) : [m];
      for (const it of items) {
        if (it.type === "hello") this.hello = it as never;
        if (it.type === "session" && this.hello) this.hello = { ...this.hello, session: it.session as SessionInfo };
        if (it.type === "quote" && it.symbol) this.quotes.set(it.symbol, it.data as Quote);
        if (it.type === "alert") {
          const evs = it.events as AlertEvent[];
          this.alertEvents = [...evs.slice().reverse(), ...this.alertEvents].slice(0, 50);
          evs.forEach(notify);
        }
        if (it.type === "alerts_ack") this.alertError = null;
        if (it.type === "error" && it.op === "alerts") this.alertError = String(it.message);
        this.listeners.forEach((f) => f(it));
      }
      if (items.some((i) => ["hello", "session", "alert", "alerts_ack", "error"].includes(i.type))) this.stateSubs.forEach((f) => f());
    };
    ws.onclose = () => {
      this.ws = null;
      this.setState("closed");
      const delay = Math.min(30_000, 1000 * 2 ** this.attempts++) + Math.random() * 500;
      this.timer = setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  send(obj: unknown) {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(obj));
  }

  subscribe(symbol: string): () => void {
    this.connect();
    const n = this.counts.get(symbol) ?? 0;
    this.counts.set(symbol, n + 1);
    if (n === 0) this.send({ op: "subscribe", symbols: [symbol] });
    return () => {
      const c = (this.counts.get(symbol) ?? 1) - 1;
      if (c <= 0) {
        this.counts.delete(symbol);
        this.quotes.delete(symbol);
        this.send({ op: "unsubscribe", symbols: [symbol] });
      } else this.counts.set(symbol, c);
    };
  }

  /** Rules live in this tab's memory and on this connection only; they are re-sent after a reconnect (re-arming them). */
  setAlertRules(rules: AlertRule[]) {
    this.connect();
    this.rules = rules;
    this.send({ op: "alerts", rules });
    this.stateSubs.forEach((f) => f());
  }

  on(fn: (m: Msg) => void): () => void {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  onState(fn: () => void): () => void {
    this.stateSubs.add(fn);
    return () => {
      this.stateSubs.delete(fn);
    };
  }

  resume() {
    if (!this.ws) {
      this.attempts = 0;
      this.connect();
    }
  }
}

/** Browser notification for a fired alert, only if the visitor granted permission. Nothing is sent through a push service. */
function notify(e: AlertEvent) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const opts = { body: e.body, tag: `smbt-${e.rule_id}`, icon: "/icons/icon-192.png" };
  const fallback = () => void new Notification(e.title, opts);
  if (!navigator.serviceWorker) return fallback();
  navigator.serviceWorker.getRegistration().then((r) => (r ? r.showNotification(e.title, opts) : fallback()), fallback);
}

export const live = new LiveClient();

if (typeof window !== "undefined") {
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && live.resume());
  window.addEventListener("online", () => live.resume());
}

export function useConnection() {
  return useSyncExternalStore(
    (cb) => {
      live.connect();
      return live.onState(cb);
    },
    () => `${live.state}|${live.hello?.session.session ?? ""}`,
    () => "closed|",
  );
}

/** Session alert rules, fired events and the last rule error; re-renders on change. */
export function useAlerts() {
  useSyncExternalStore(
    (cb) => {
      live.connect();
      return live.onState(cb);
    },
    () => `${live.rules.length}|${live.alertEvents.length}|${live.alertEvents[0]?.fired_at ?? ""}|${live.alertError ?? ""}|${live.state}`,
    () => "",
  );
  return { rules: live.rules, events: live.alertEvents, error: live.alertError, connected: live.state === "open" };
}

export function useSession(): SessionInfo | null {
  useConnection();
  return live.hello?.session ?? null;
}

/** Latest quote for a symbol. Server pushes at most every 250 ms; renders are additionally coalesced per frame. */
export function useQuote(symbol: string | null | undefined): Quote | null {
  const [q, setQ] = useState<Quote | null>(null);
  useEffect(() => {
    if (!symbol) return;
    setQ(live.quotes.get(symbol) ?? null);
    let frame = 0;
    let latest: Quote | null = null;
    const off = live.on((m) => {
      if (m.type === "quote" && m.symbol === symbol) {
        latest = m.data as Quote;
        if (!frame) frame = requestAnimationFrame(() => {
          frame = 0;
          setQ(latest);
        });
      }
    });
    const unsub = live.subscribe(symbol);
    return () => {
      off();
      unsub();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [symbol]);
  return q;
}

/** Calls onBar for live 1-minute bars (provisional and official) without re-rendering React. */
export function useLiveBars(symbol: string | null, onBars: (bars: MinuteBar[], replace: boolean) => void) {
  const cb = useRef(onBars);
  cb.current = onBars;
  useEffect(() => {
    if (!symbol) return;
    const off = live.on((m) => {
      if (m.symbol !== symbol) return;
      if (m.type === "bars") cb.current((m.bars as MinuteBar[]) ?? [], true);
      if (m.type === "bar") cb.current([m.bar as MinuteBar], false);
    });
    const unsub = live.subscribe(symbol);
    return () => {
      off();
      unsub();
    };
  }, [symbol]);
}

export function useLiveScores(symbol: string | null): LiveScores | null {
  const [s, setS] = useState<LiveScores | null>(null);
  useEffect(() => {
    if (!symbol) return;
    return live.on((m) => {
      if (m.type === "scores" && m.symbol === symbol) setS(m.data as LiveScores);
    });
  }, [symbol]);
  return s;
}
