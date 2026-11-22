"use client";

import { useEffect, useState } from "react";
import { Badge, ErrorBox, Note, Panel, Spinner } from "@/components/ui";
import { api, type AlertEvent, type AlertKinds, type AlertRule } from "@/lib/api";
import { useApi } from "@/lib/useApi";

const COOLDOWNS = [
  { v: 60, l: "at most hourly" },
  { v: 360, l: "at most every 6h" },
  { v: 1440, l: "at most daily" },
  { v: 10080, l: "at most weekly" },
];

function b64ToUint8(b64: string) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function PushControl() {
  const key = useApi<{ public_key: string | null }>("/push/key");
  const [state, setState] = useState<string>("checking");
  useEffect(() => {
    (async () => {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) return setState("unsupported");
      const reg = await navigator.serviceWorker.getRegistration();
      const sub = await reg?.pushManager.getSubscription();
      setState(sub ? "on" : Notification.permission === "denied" ? "denied" : "off");
    })().catch(() => setState("unsupported"));
  }, []);
  const enable = async () => {
    if (!key.data?.public_key) return;
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return setState("denied");
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(key.data.public_key) });
    await api("/push/subscribe", { method: "POST", json: sub.toJSON() });
    setState("on");
  };
  const disable = async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (sub) {
      await api("/push/unsubscribe", { method: "POST", json: { endpoint: sub.endpoint } });
      await sub.unsubscribe();
    }
    setState("off");
  };
  return (
    <Panel title="Push notifications on this device">
      {key.data && !key.data.public_key && <p className="text-sm text-warn">Server push is not configured (set VAPID keys; see docs/DEPLOY.md). Alerts still appear in the history below.</p>}
      {state === "unsupported" && (
        <p className="text-sm text-muted">This browser does not support Web Push here. On iPhone: iOS 16.4+, open in Safari, Share → Add to Home Screen, then open the installed app and enable here.</p>
      )}
      {state === "denied" && <p className="text-sm text-down">Notifications are blocked for this site. Re-enable them in system/browser settings.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {state === "off" && key.data?.public_key && <button onClick={() => enable().catch((e) => alert(e.message))} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white">Enable notifications</button>}
        {state === "on" && (
          <>
            <Badge tone="up">enabled</Badge>
            <button onClick={() => api<{ delivered: number }>("/push/test", { method: "POST" }).then((r) => alert(`Sent to ${r.delivered} device(s)`))} className="rounded-lg border border-line px-3 py-1.5 text-sm">Send test</button>
            <button onClick={disable} className="rounded-lg border border-line px-3 py-1.5 text-sm">Disable</button>
          </>
        )}
      </div>
    </Panel>
  );
}

function RuleForm({ kinds, onSaved }: { kinds: AlertKinds; onSaved: () => void }) {
  const [kind, setKind] = useState("score_above");
  const [p, setP] = useState<Record<string, string>>({ score: "bottom", threshold: "85" });
  const [name, setName] = useState("");
  const [cooldown, setCooldown] = useState(1440);
  const [err, setErr] = useState<Error | null>(null);
  const set = (k: string, v: string) => setP({ ...p, [k]: v });
  const field = "rounded-lg border border-line bg-panel px-2 py-1.5 text-sm";
  const defaults: Record<string, Record<string, string>> = {
    score_above: { score: "bottom", threshold: "85" },
    score_below: { score: "trend", threshold: "30" },
    vix_range: { min: "30", max: "100" },
    breadth_extreme: { metric: "pct_above_50", op: "below", value: "15" },
    divergence: { direction: "bullish", symbol: "" },
    regime_change: { symbol: "" },
    watchlist_setup: { direction: "any" },
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const params: Record<string, unknown> = Object.fromEntries(Object.entries(p).filter(([, v]) => v !== "").map(([k, v]) => [k, isNaN(Number(v)) ? v : Number(v)]));
      await api("/alerts", { method: "POST", json: { name: name || kinds.kinds[kind], kind, params, cooldown_minutes: cooldown } });
      setName("");
      onSaved();
    } catch (e) {
      setErr(e as Error);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <select aria-label="Alert type" value={kind} onChange={(e) => { setKind(e.target.value); setP(defaults[e.target.value]); }} className={field}>
          {Object.entries(kinds.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input aria-label="Alert name" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} className={field} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {(kind === "score_above" || kind === "score_below") && (
          <>
            <select aria-label="Score" value={p.score} onChange={(e) => set("score", e.target.value)} className={field}>
              {Object.entries(kinds.scores).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {kind === "score_above" ? "crosses above" : "falls below"}
            <input aria-label="Threshold" type="number" min={0} max={100} value={p.threshold} onChange={(e) => set("threshold", e.target.value)} className={field + " w-20"} />
          </>
        )}
        {kind === "vix_range" && (
          <>VIX between <input aria-label="Min" type="number" value={p.min} onChange={(e) => set("min", e.target.value)} className={field + " w-20"} /> and <input aria-label="Max" type="number" value={p.max} onChange={(e) => set("max", e.target.value)} className={field + " w-20"} /></>
        )}
        {kind === "breadth_extreme" && (
          <>
            <select aria-label="Metric" value={p.metric} onChange={(e) => set("metric", e.target.value)} className={field}>{Object.entries(kinds.breadth_metrics).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <select aria-label="Direction" value={p.op} onChange={(e) => set("op", e.target.value)} className={field}><option value="below">below</option><option value="above">above</option></select>
            <input aria-label="Value" type="number" value={p.value} onChange={(e) => set("value", e.target.value)} className={field + " w-20"} />
          </>
        )}
        {(kind === "divergence" || kind === "watchlist_setup") && (
          <select aria-label="Direction" value={p.direction} onChange={(e) => set("direction", e.target.value)} className={field}>
            {kind === "watchlist_setup" && <option value="any">any</option>}<option value="bullish">bullish</option><option value="bearish">bearish</option>
          </select>
        )}
        {kind !== "watchlist_setup" && <input aria-label="Symbol" placeholder="Symbol (default SPY)" value={p.symbol ?? ""} onChange={(e) => set("symbol", e.target.value.toUpperCase())} className={field + " w-40"} />}
        <select aria-label="Frequency" value={cooldown} onChange={(e) => setCooldown(+e.target.value)} className={field}>
          {COOLDOWNS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
        </select>
        <button className="rounded-lg bg-accent px-3 py-1.5 font-medium text-white">Add alert</button>
      </div>
      {err && <ErrorBox error={err} />}
    </form>
  );
}

export default function AlertsPage() {
  const kinds = useApi<AlertKinds>("/alerts/kinds");
  const rules = useApi<AlertRule[]>("/alerts");
  const events = useApi<AlertEvent[]>("/alerts/events");
  const update = async (r: AlertRule, patch: Partial<AlertRule>) => {
    await api(`/alerts/${r.id}`, { method: "PUT", json: { name: r.name, kind: r.kind, params: r.params, cooldown_minutes: r.cooldown_minutes, enabled: r.enabled, ...patch } });
    rules.reload();
  };
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Alerts</h1>
      <PushControl />
      <Panel title="New alert">{kinds.data ? <RuleForm kinds={kinds.data} onSaved={rules.reload} /> : <Spinner />}
        <Note>Alerts fire on the transition into a condition and must re-arm (move back past the threshold) before firing again; frequency caps repeats further. Evaluated after each end-of-day update.</Note>
      </Panel>
      <Panel title="Your alerts" right={<button onClick={() => api("/alerts/evaluate", { method: "POST" }).then(() => { rules.reload(); events.reload(); })}>Evaluate now</button>}>
        {rules.error && <ErrorBox error={rules.error} />}
        {rules.data?.length === 0 && <p className="text-sm text-muted">No alerts yet.</p>}
        <ul className="divide-y divide-line">
          {rules.data?.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="flex-1 font-medium">{r.name}</span>
              <span className="text-xs text-muted">{JSON.stringify(r.params)}</span>
              {!r.armed && <Badge tone="warn" title="Fired; waits to re-arm">waiting to re-arm</Badge>}
              {r.last_fired_at && <span className="text-xs text-muted">last {new Date(r.last_fired_at).toLocaleString()}</span>}
              <button onClick={() => update(r, { enabled: !r.enabled })} className="rounded border border-line px-2 py-0.5 text-xs">{r.enabled ? "Pause" : "Resume"}</button>
              <button onClick={() => api(`/alerts/${r.id}`, { method: "DELETE" }).then(rules.reload)} className="rounded border border-down/40 px-2 py-0.5 text-xs text-down">Delete</button>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Recent alerts">
        {events.data?.length === 0 && <p className="text-sm text-muted">Nothing fired yet.</p>}
        <ul className="space-y-2">
          {events.data?.map((e) => (
            <li key={e.id} className="rounded-lg bg-panel2 p-2 text-sm">
              <div className="flex justify-between gap-2"><b>{e.title}</b><span className="text-xs text-muted">{new Date(e.fired_at).toLocaleString()}</span></div>
              <div className="text-xs text-muted">{e.body} {e.delivered ? `· pushed to ${e.delivered}` : ""}</div>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
