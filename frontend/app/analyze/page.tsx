"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { PriceChart } from "@/components/charts";
import { Badge, ErrorBox, KindTag, Note, Panel, Spinner, Td, Th, toneText } from "@/components/ui";
import { api, type Analysis, type ChartData, type SignalEvent, type SymbolInfo } from "@/lib/api";
import { STATE_TONE, num, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const enc = encodeURIComponent;
const IND_LABELS: Record<string, [string, (v: number | null) => string]> = {
  close: ["Close", (v) => num(v)],
  sma50: ["50-day avg", (v) => num(v)],
  sma200: ["200-day avg", (v) => num(v)],
  dist200: ["Distance from 200d", (v) => pct(v, 1, true)],
  drawdown: ["Drawdown from 52w high", (v) => pct(v, 1)],
  rsi14: ["RSI(14)", (v) => num(v, 1)],
  stochrsi_k: ["Stoch RSI %K", (v) => num(v, 0)],
  macd_hist: ["MACD histogram", (v) => num(v, 3)],
  bb_pctb: ["Bollinger %B", (v) => num(v, 2)],
  adx: ["ADX", (v) => num(v, 1)],
  atr_pct: ["ATR % of price", (v) => pct(v, 2)],
  hv20: ["Hist. volatility 20d", (v) => pct(v, 1)],
  roc20: ["20d return", (v) => pct(v, 1, true)],
  roc63: ["63d return", (v) => pct(v, 1, true)],
  vol_ratio: ["Volume vs 50d avg", (v) => num(v, 2) + "×"],
  rs63: ["Rel. strength vs benchmark 63d", (v) => pct(v, 1, true)],
  rs126: ["Rel. strength vs benchmark 126d", (v) => pct(v, 1, true)],
  r2_63: ["Trend R² (63d)", (v) => num(v, 2)],
};

function Analyze() {
  const params = useSearchParams();
  const router = useRouter();
  const s = (params.get("s") || "").toUpperCase();
  const syms = useApi<{ symbols: SymbolInfo[] }>("/symbols");
  const a = useApi<Analysis>(s ? `/analyze/${enc(s)}` : null);
  const chart = useApi<ChartData>(s ? `/chart/${enc(s)}` : null);
  const wl = useApi<string[]>("/watchlist");
  const [q, setQ] = useState(s);
  const events = useMemo<SignalEvent[]>(
    () =>
      (a.data?.recent_divergences ?? []).map((d) => ({
        date: d.confirmed,
        type: `${d.type}_divergence`,
        label: `RSI ${d.type} divergence`,
        triggered_by: [{ label: "Price at pivots", value: d.price }, { label: "RSI at pivots", value: d.osc }],
        forward_realized: {},
      })),
    [a.data],
  );
  const inWl = wl.data?.includes(s);

  return (
    <div className="space-y-4">
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) router.push(`/analyze/?s=${enc(q.trim().toUpperCase())}`);
        }}
      >
        <h1 className="mr-2 text-lg font-semibold">Analyze</h1>
        <input list="symlist" value={q} onChange={(e) => setQ(e.target.value)} placeholder="SPY, QQQ, SMH, NVDA, GOOG, AMZN…" aria-label="Ticker" autoCapitalize="characters" className="w-56 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm" />
        <datalist id="symlist">{syms.data?.symbols.map((x) => <option key={x.symbol} value={x.symbol}>{x.name}</option>)}</datalist>
        <button className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white">Analyze</button>
        {s && (
          <button
            type="button"
            onClick={async () => {
              await api(`/watchlist/${enc(s)}`, { method: inWl ? "DELETE" : "POST" });
              wl.reload();
            }}
            className="rounded-lg border border-line px-3 py-1.5 text-sm"
          >
            {inWl ? "★ Watching" : "☆ Watchlist"}
          </button>
        )}
      </form>
      {!s && <Note>Enter a supported ticker. Supported = any symbol with ingested price history (see Settings &amp; Data).</Note>}
      {a.error && <ErrorBox error={a.error} onRetry={a.reload} />}
      {s && !a.data && !a.error && <Spinner label={`Analyzing ${s}`} />}
      {a.data && (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title={`${a.data.symbol} · ${a.data.name}`} right={<span className="num">{a.data.as_of}</span>} className="lg:col-span-2">
              <div className="flex flex-wrap items-center gap-3">
                <span className={`text-2xl font-semibold ${toneText[STATE_TONE[a.data.state] ?? "muted"]}`}>{a.data.state_label}</span>
                <KindTag kind="heuristic" />
              </div>
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm">{a.data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
              <div className="mt-4 text-sm font-semibold">After past occurrences of this state on {a.data.symbol}</div>
              <div className="scroll-x">
                <table className="mt-1 w-full text-xs">
                  <thead><tr><Th>Horizon</Th><Th>Samples</Th><Th>Median</Th><Th>Hit rate</Th><Th>Unconditional median</Th><Th>Unconditional hit</Th></tr></thead>
                  <tbody>
                    {Object.entries(a.data.state_forward_stats).map(([h, v]) => (
                      <tr key={h}>
                        <Td>{h} sessions</Td><Td>{v.n_samples}</Td><Td className={signedClass(v.median)}>{pct(v.median, 1, true)}</Td><Td>{pct(v.hit_rate, 0)}</Td>
                        <Td className={signedClass(v.unconditional_median)}>{pct(v.unconditional_median, 1, true)}</Td><Td>{pct(v.unconditional_hit_rate, 0)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Note>{a.data.state_stats_note}</Note>
            </Panel>
            <Panel title="Context">
              <dl className="space-y-2 text-sm">
                {a.data.market && (
                  <div>
                    <dt className="text-xs text-muted">Market ({a.data.market.benchmark})</dt>
                    <dd>{a.data.market.regime} · Bottom {num(a.data.market.bottom, 0)} · Top risk {num(a.data.market.top, 0)}</dd>
                  </div>
                )}
                {a.data.sector && (
                  <div>
                    <dt className="text-xs text-muted">Sector ({a.data.sector.sector ?? ""} via {a.data.sector.etf})</dt>
                    <dd>
                      {a.data.sector.note ?? (
                        <>
                          Sector vs market 63d <span className={signedClass(a.data.sector.sector_rs63_vs_benchmark)}>{pct(a.data.sector.sector_rs63_vs_benchmark, 1, true)}</span> · stock vs sector{" "}
                          <span className={signedClass(a.data.sector.stock_rs63_vs_sector)}>{pct(a.data.sector.stock_rs63_vs_sector, 1, true)}</span>
                        </>
                      )}
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-muted">Relative valuation</dt>
                  <dd className="text-xs text-muted">{a.data.valuation.note}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Recent divergences</dt>
                  <dd className="space-y-0.5 text-xs">
                    {a.data.recent_divergences.length ? a.data.recent_divergences.slice(-3).map((d) => (
                      <div key={d.confirmed + d.type}><Badge tone={d.type === "bullish" ? "up" : "warn"}>{d.type}</Badge> confirmed {d.confirmed}</div>
                    )) : "none"}
                  </dd>
                </div>
                <div className="text-xs text-muted">Source: {a.data.source}</div>
              </dl>
            </Panel>
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Panel title="Chart">{chart.data ? <PriceChart data={chart.data} events={events} overlays={["sma20", "sma50", "sma200", "avwap_from_252d_low"]} /> : <Spinner />}<Note>Overlays: 20/50/200-day averages and VWAP anchored at the 252-day low{chart.data?.avwap_anchor && ` (${chart.data.avwap_anchor})`}.{chart.data?.volume_profile && ` Volume profile (126d): POC ${num(chart.data.volume_profile.poc)}, value area ${num(chart.data.volume_profile.value_area_low)}–${num(chart.data.volume_profile.value_area_high)}.`}</Note></Panel>
            <Panel title="Indicators">
              <table className="w-full text-xs">
                <thead><tr><Th>Indicator</Th><Th className="text-right">Value</Th><Th className="text-right">Hist. pct</Th></tr></thead>
                <tbody>
                  {Object.entries(IND_LABELS).map(([k, [label, f]]) => (
                    <tr key={k}><Td>{label}</Td><Td className="text-right">{f(a.data!.indicators[k] ?? null)}</Td><Td className="text-right text-muted">{a.data!.percentiles[k] !== undefined ? pct(a.data!.percentiles[k], 0) : ""}</Td></tr>
                  ))}
                </tbody>
              </table>
              <Note>Hist. pct = where today&apos;s value sits in this symbol&apos;s own history up to today.</Note>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Spinner />}>
      <Analyze />
    </Suspense>
  );
}
