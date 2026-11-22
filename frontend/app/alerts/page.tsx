"use client";

import { useEffect, useState } from "react";
import { Badge, ErrorBox, Note, Panel, Spinner } from "@/components/ui";
import type { AlertKinds, AlertRule } from "@/lib/api";
import { live, useAlerts } from "@/lib/live";
import { useApi } from "@/lib/useApi";
import { useWatchlist } from "@/lib/watchlist";

const COOLDOWNS = [
  { v: 60, l: "at most hourly" },
  { v: 360, l: "at most every 6h" },
  { v: 1440, l: "at most daily" },
  { v: 10080, l: "at most weekly" },
];

function NotifyControl() {
  const [perm, setPerm] = useState<string>("checking");
  useEffect(() => setPerm(typeof Notification === "undefined" ? "unsupported" : Notification.permission), []);
  return (
    <Panel title="Notifications on this device">
      {perm === "unsupported" && (
        <p className="text-sm text-muted">This browser has no notification support here. On iPhone (iOS 16.4+): Safari → Share → Add to Home Screen, then open the installed app. Alerts still list below.</p>
      )}
      {perm === "denied" && <p className="text-sm text-down">Notifications are blocked for this site. Re-enable them in system or browser settings.</p>}
      {perm === "default" && (
        <button onClick={() => Notification.requestPermission().then(setPerm)} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white">
          Allow notifications
        </button>
      )}
      {perm === "granted" && <Badge tone="up">allowed</Badge>}
      <Note>
        Alerts are evaluated on the server once a minute for this open connection only and shown here and as a local notification. No push subscription, account or rule is stored, so
        nothing fires after you close or refresh the page.
      </Note>
    </Panel>
  );
}

function RuleForm({ kinds, rules }: { kinds: AlertKinds; rules: AlertRule[] }) {
  const wl = useWatchlist();
  const [kind, setKind] = useState("score_above");
  const [p, setP] = useState<Record<string, string>>({ score: "bottom", threshold: "85" });
  const [name, setName] = useState("");
  const [cooldown, setCooldown] = useState(1440);
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
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const params: Record<string, unknown> = Object.fromEntries(Object.entries(p).filter(([, v]) => v !== "").map(([k, v]) => [k, isNaN(Number(v)) ? v : Number(v)]));
    if (kind === "watchlist_setup") params.symbols = wl;
    const id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    live.setAlertRules([...rules, { id, name: name || kinds.kinds[kind], kind, params, cooldown_minutes: cooldown }]);
    setName("");
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
        {kind === "watchlist_setup" && <span className="text-xs text-muted">{wl.length ? `watching ${wl.join(", ")}` : "add symbols with ☆ on Analyze first"}</span>}
        {kind !== "watchlist_setup" && <input aria-label="Symbol" placeholder="Symbol (default SPY)" value={p.symbol ?? ""} onChange={(e) => set("symbol", e.target.value.toUpperCase())} className={field + " w-40"} />}
        <select aria-label="Frequency" value={cooldown} onChange={(e) => setCooldown(+e.target.value)} className={field}>
          {COOLDOWNS.map((c) => <option key={c.v} value={c.v}>{c.l}</option>)}
        </select>
        <button disabled={kind === "watchlist_setup" && !wl.length} className="rounded-lg bg-accent px-3 py-1.5 font-medium text-white disabled:opacity-50">Add alert</button>
      </div>
    </form>
  );
}

export default function AlertsPage() {
  const kinds = useApi<AlertKinds>("/alerts/kinds");
  const { rules, events, error, connected } = useAlerts();
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Alerts (this session)</h1>
      <NotifyControl />
      <Panel title="New alert">
        {kinds.error && <ErrorBox error={kinds.error} onRetry={kinds.reload} />}
        {kinds.data ? <RuleForm kinds={kinds.data} rules={rules} /> : !kinds.error && <Spinner />}
        <Note>
          Alerts fire on the transition into a condition and must re-arm (move back past the threshold) before firing again; the frequency setting caps repeats and each session is limited
          to 20 alerts a day. Score alerts use the provisional intraday value during market hours and the confirmed close otherwise; the alert text says which.
        </Note>
      </Panel>
      <Panel title="Active rules" right={!connected && <Badge tone="warn">reconnecting: rules resume when the connection returns</Badge>}>
        {error && <ErrorBox error={new Error(`Rules rejected: ${error}`)} />}
        {rules.length === 0 && <p className="text-sm text-muted">No alerts yet.</p>}
        <ul className="divide-y divide-line">
          {rules.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="flex-1 font-medium">{r.name}</span>
              <span className="text-xs text-muted">{JSON.stringify(r.params)}</span>
              <button onClick={() => live.setAlertRules(rules.filter((x) => x.id !== r.id))} className="rounded border border-down/40 px-2 py-0.5 text-xs text-down">
                Remove
              </button>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Fired this session">
        {events.length === 0 && <p className="text-sm text-muted">Nothing fired yet.</p>}
        <ul className="space-y-2">
          {events.map((e, i) => (
            <li key={e.rule_id + e.fired_at + i} className="rounded-lg bg-panel2 p-2 text-sm">
              <div className="flex justify-between gap-2"><b>{e.title}</b><span className="text-xs text-muted">{new Date(e.fired_at).toLocaleString()}</span></div>
              <div className="text-xs text-muted">{e.body}</div>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
