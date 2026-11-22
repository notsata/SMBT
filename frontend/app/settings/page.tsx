"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { applyTheme } from "@/components/Shell";
import { Badge, ErrorBox, Note, Panel, Segmented, Spinner, Td, Th } from "@/components/ui";
import { api, type Status } from "@/lib/api";
import { useApi } from "@/lib/useApi";

function Theme() {
  const [t, setT] = useState<"dark" | "light" | "system">("dark");
  useEffect(() => {
    try {
      setT((localStorage.getItem("smbt-theme") as "dark" | "light" | "system") || "dark");
    } catch {
      /* ignore */
    }
  }, []);
  return (
    <Segmented label="Theme" value={t} onChange={(v) => { setT(v); applyTheme(v); }} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "System" }]} />
  );
}

function Watchlist() {
  const wl = useApi<string[]>("/watchlist");
  const [s, setS] = useState("");
  const [err, setErr] = useState("");
  return (
    <Panel title="Watchlist (synced across devices)">
      <form onSubmit={async (e) => {
        e.preventDefault();
        if (!s.trim()) return;
        try {
          await api(`/watchlist/${encodeURIComponent(s.trim().toUpperCase())}`, { method: "POST" });
          setS("");
          setErr("");
          wl.reload();
        } catch (x) {
          setErr((x as Error).message);
        }
      }} className="mb-2 flex gap-2">
        <input value={s} onChange={(e) => setS(e.target.value)} placeholder="Add ticker" aria-label="Add ticker" className="rounded-lg border border-line bg-panel px-2 py-1.5 text-sm" />
        <button className="rounded-lg border border-line px-3 text-sm">Add</button>
      </form>
      <div className="flex flex-wrap gap-2">
        {wl.data?.map((x) => (
          <span key={x} className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-sm">
            <Link href={`/analyze/?s=${encodeURIComponent(x)}`} className="text-accent">{x}</Link>
            <button aria-label={`Remove ${x}`} onClick={() => api(`/watchlist/${encodeURIComponent(x)}`, { method: "DELETE" }).then(wl.reload)} className="text-muted">✕</button>
          </span>
        ))}
        {wl.data?.length === 0 && <span className="text-sm text-muted">Empty.</span>}
      </div>
      {err && <p className="mt-2 text-xs text-down">{err}</p>}
    </Panel>
  );
}

function Admin({ onDone }: { onDone: () => void }) {
  const ing = useApi<{ state: string; errors?: number; jobs?: number; error?: string }>("/admin/ingest");
  const [series, setSeries] = useState("AAII_SPREAD");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    if (ing.data?.state !== "running") return;
    const t = setInterval(() => ing.reload(), 5000);
    return () => clearInterval(t);
  }, [ing]);
  return (
    <Panel title="Data operations">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button onClick={() => api("/admin/ingest", { method: "POST" }).then(() => setTimeout(ing.reload, 500), (e: Error) => setMsg(e.message))} className="rounded-lg bg-accent px-3 py-1.5 font-medium text-white">Run ingestion now</button>
        <span className="text-xs text-muted">status: {ing.data?.state}{ing.data?.jobs !== undefined && ` · ${ing.data.jobs} jobs, ${ing.data.errors} errors`}{ing.data?.error && ` · ${ing.data.error}`}</span>
        <button onClick={() => api<{ bars: number }>("/admin/sample", { method: "POST" }).then((r) => { setMsg(`Loaded ${r.bars} sample bars`); onDone(); }, (e: Error) => setMsg(e.message))} className="rounded-lg border border-line px-3 py-1.5">Load sample data</button>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <span>Import CSV (date,value[,available_at]) for</span>
        <select aria-label="Series" value={series} onChange={(e) => setSeries(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1">
          {["AAII_SPREAD", "NAAIM", "PCR_EQUITY", "PCR_TOTAL", "SHORT_INTEREST", "CAPE"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file" className="text-xs" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          try {
            const r = await api<{ rows: number }>(`/admin/import/${series}`, { method: "POST", body: await f.text(), headers: { "Content-Type": "text/csv" } });
            setMsg(`Imported ${r.rows} rows into ${series}`);
            onDone();
          } catch (err) {
            setMsg((err as Error).message);
          }
        }} />
      </div>
      {msg && <p className="mt-2 text-xs text-accent">{msg}</p>}
      <Note>Use imports for licensed or membership data (AAII, NAAIM, put/call, short interest, CAPE). Provide available_at (publication date) when you know it; otherwise a conservative lag is applied.</Note>
    </Panel>
  );
}

export default function SettingsPage() {
  const st = useApi<Status>("/status");
  const me = useApi<{ authenticated: boolean; auth_enabled: boolean }>("/auth/me");
  const s = st.data;
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Settings &amp; data</h1>
      <Panel title="App">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Theme />
          {me.data?.auth_enabled ? (
            <button onClick={() => api("/auth/logout", { method: "POST" }).then(() => (location.href = "/login/"))} className="rounded-lg border border-line px-3 py-1.5">Sign out this device</button>
          ) : (
            <Badge tone="warn">Authentication disabled (APP_ACCESS_TOKEN not set)</Badge>
          )}
        </div>
        <Note>Install: iPhone Safari → Share → Add to Home Screen. Windows Chrome/Edge → install icon in the address bar. All devices share the same backend data, watchlist and alerts.</Note>
      </Panel>
      <Watchlist />
      <Admin onDone={st.reload} />
      {st.error && <ErrorBox error={st.error} />}
      {!s && !st.error && <Spinner />}
      {s && (
        <>
          <Panel title="Providers">
            <div className="flex flex-wrap gap-2 text-xs">
              {Object.entries(s.providers).map(([k, v]) => <Badge key={k} tone={v ? "up" : "muted"}>{k}: {v ? "configured" : "not configured"}</Badge>)}
              <Badge tone={s.breadth.available ? "up" : "warn"}>breadth: {s.breadth.available ? `${s.breadth.with_data} members` : "unavailable"}</Badge>
            </div>
            {s.breadth.note && <Note>{s.breadth.note}</Note>}
          </Panel>
          <Panel title="Price data" right={`${s.symbols_loaded} symbols loaded`}>
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Symbol</Th><Th>From</Th><Th>Last bar</Th><Th>Rows</Th><Th>Source</Th><Th>Fetched</Th></tr></thead>
                <tbody>{s.prices.map((p) => (
                  <tr key={p.symbol}><Td>{p.symbol}</Td><Td>{p.first}</Td><Td>{p.last} {p.stale && <Badge tone="warn">stale</Badge>}</Td><Td>{p.rows}</Td><Td className="max-w-[18rem] truncate" >{p.source}</Td><Td className="text-muted">{p.fetched_at.slice(0, 16)}</Td></tr>
                ))}</tbody>
              </table>
            </div>
          </Panel>
          <Panel title="Macro & sentiment series (point-in-time)">
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Series</Th><Th>Name</Th><Th>Freq</Th><Th>Last obs</Th><Th>Known from</Th><Th>Source</Th><Th>Notes</Th></tr></thead>
                <tbody>{s.series.map((x) => (
                  <tr key={x.id} className={x.available ? "" : "text-muted"}>
                    <Td>{x.id}</Td><Td className="whitespace-normal">{x.name}</Td><Td>{x.freq}</Td><Td>{x.last_obs ?? <Badge tone="muted">unavailable</Badge>}</Td><Td>{x.available_at ?? ""}</Td>
                    <Td className="whitespace-normal">{x.source ?? x.default_source}</Td><Td className="whitespace-normal text-muted">{x.note}</Td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </Panel>
          {s.recent_errors.length > 0 && (
            <Panel title="Recent ingestion errors">
              <ul className="space-y-1 text-xs">{s.recent_errors.map((e, i) => <li key={i}><b>{e.source}:{e.target}</b> <span className="text-muted">{e.at.slice(0, 16)}</span> — {e.message}</li>)}</ul>
            </Panel>
          )}
          <Panel title="Metrics that need licensed data">
            <ul className="space-y-1 text-xs text-muted">{Object.entries(s.unavailable).map(([k, v]) => <li key={k}><b className="text-fg/80">{k.replaceAll("_", " ")}</b>: {v}</li>)}</ul>
          </Panel>
        </>
      )}
    </div>
  );
}
