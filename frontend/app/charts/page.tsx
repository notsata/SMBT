"use client";

import { useMemo, useState } from "react";
import { LineChart, type LineSpec } from "@/components/charts";
import { ErrorBox, Note, Panel, Spinner } from "@/components/ui";
import type { Symbols } from "@/lib/api";
import { useApi } from "@/lib/useApi";

type Ind = { symbol: string; t: string[]; series: Record<string, (number | null)[]>; labels: Record<string, string>; available: string[] };
const PRESETS: Record<string, string[]> = {
  "Scores": ["bottom", "top", "trend"],
  "Momentum": ["rsi14", "macd_hist", "stochrsi_k"],
  "Breadth": ["pct_above_50", "mcclellan_osc", "nhnl_pct"],
  "Sentiment": ["vix", "vix_term", "fear_greed"],
  "Macro": ["credit_spread", "curve_10y2y", "macro_risk"],
};

export default function ChartsPage() {
  const syms = useApi<Symbols>("/symbols");
  const [symbol, setSymbol] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>(["bottom", "top", "rsi14"]);
  const sym = symbol ?? syms.data?.default ?? null;
  const ind = useApi<Ind>(sym ? `/indicators/${encodeURIComponent(sym)}?names=close,${picked.join(",")}` : null);
  const [full, setFull] = useState(false);
  const lines = useMemo<LineSpec[]>(() => {
    if (!ind.data) return [];
    const out: LineSpec[] = [];
    const close = ind.data.series.close;
    if (close) out.push({ name: `${ind.data.symbol} close`, values: close, pane: 0 });
    picked.filter((k) => ind.data!.series[k]).forEach((k, i) => out.push({ name: ind.data!.labels[k], values: ind.data!.series[k], pane: i + 1 }));
    return out;
  }, [ind.data, picked]);
  const toggle = (k: string) => setPicked(picked.includes(k) ? picked.filter((x) => x !== k) : [...picked, k].slice(-5));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Indicator comparison</h1>
        <select aria-label="Symbol" value={sym ?? ""} onChange={(e) => setSymbol(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1 text-xs">
          {syms.data && [...new Set([...syms.data.benchmarks, ...syms.data.universe])].map((s) => <option key={s}>{s}</option>)}
        </select>
        {Object.entries(PRESETS).map(([name, ks]) => (
          <button key={name} onClick={() => setPicked(ks)} className="rounded-lg border border-line px-2 py-1 text-xs text-muted hover:text-fg">{name}</button>
        ))}
      </div>
      {ind.error && <ErrorBox error={ind.error} />}
      <Panel title="Synchronized panes" right={<button onClick={() => setFull(!full)}>{full ? "Exit full screen" : "Full screen"}</button>}>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {ind.data?.available.filter((k) => k !== "close").map((k) => (
            <button key={k} aria-pressed={picked.includes(k)} onClick={() => toggle(k)} className={`rounded-md border px-2 py-0.5 text-[11px] ${picked.includes(k) ? "border-accent bg-accent/10 text-accent" : "border-line text-muted"}`}>
              {k}
            </button>
          ))}
        </div>
        <div className={full ? "fixed inset-0 z-50 bg-panel p-2" : ""}>
          {full && <button className="absolute right-3 top-3 z-10 rounded border border-line bg-panel px-2 text-xs" onClick={() => setFull(false)}>Close</button>}
          {ind.data ? <LineChart t={ind.data.t} lines={lines} height={full ? (typeof window !== "undefined" ? window.innerHeight - 20 : 800) : 140 + 130 * lines.length} /> : <Spinner />}
        </div>
        <Note>Completed sessions only (confirmed closes). Price in the top pane, one indicator per pane below, all on one time axis. Up to five indicators. Unavailable indicators (e.g. breadth without constituent data) are not offered.</Note>
      </Panel>
    </div>
  );
}
