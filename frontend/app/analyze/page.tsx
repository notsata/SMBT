"use client";

import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { PriceChart } from "@/components/charts";
import { LiveQuote } from "@/components/LiveQuote";
import { Badge, ErrorBox, Inspector, KindTag, Note, Panel, Spinner, StatusLine, Td, Th, toneText } from "@/components/ui";
import { api, type Analysis, type ChartData, type SignalEvent, type Symbols } from "@/lib/api";
import { STATE_TONE, isNum, num, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { useWatchlist, watchlist } from "@/lib/watchlist";

const enc = encodeURIComponent;
type Hit = { symbol: string; name: string; exchange?: string; provider: string };
const human = (k: string) => k.replace(/TTM$/, "").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
const fmtVal = (v: unknown) => (isNum(v) ? (Math.abs(v) >= 1e6 ? v.toExponential(3) : num(v, Math.abs(v) < 10 ? 3 : 2)) : String(v ?? "—"));

/** Provider fields shown as delivered (names humanized); nothing is renamed or recomputed. */
function KV({ obj, limit = 16 }: { obj?: Record<string, unknown>; limit?: number }) {
  const rows = Object.entries(obj ?? {}).filter(([, v]) => isNum(v)).slice(0, limit);
  if (!rows.length) return <span className="text-xs text-muted">none returned</span>;
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="truncate text-muted" title={k}>{human(k)}</dt>
          <dd className="num text-right">{fmtVal(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Rows({ rows, limit = 6 }: { rows?: Record<string, unknown>[]; limit?: number }) {
  if (!rows?.length) return <span className="text-xs text-muted">none returned</span>;
  const cols = Object.keys(rows[0]).filter((k) => k !== "symbol" && ["string", "number"].includes(typeof rows[0][k])).slice(0, limit);
  return (
    <div className="scroll-x">
      <table className="w-full text-xs">
        <thead><tr>{cols.map((c) => <Th key={c}>{human(c)}</Th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <Td key={c}>{fmtVal(r[c])}</Td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Fundamentals({ f }: { f: Analysis["fundamentals"] }) {
  const [tab, setTab] = useState<"ratios" | "earnings" | "estimates" | "dividends" | "holdings">("ratios");
  const tabs = (["ratios", "earnings", "estimates", "dividends", "holdings"] as const).filter((k) => k === "ratios" || (f[k]?.length ?? 0) > 0);
  if (!f.provider)
    return (
      <Panel title="Fundamentals">
        <Note>{f.unavailable.join(" · ")}</Note>
      </Panel>
    );
  return (
    <Panel title="Fundamentals" right={<span>{f.provider.toUpperCase()} · reported filings &amp; estimates</span>}>
      <div className="mb-2 flex flex-wrap gap-1">
        {tabs.map((k) => (
          <button key={k} onClick={() => setTab(k)} aria-pressed={tab === k} className={`rounded-md border px-2 py-0.5 text-[11px] ${tab === k ? "border-accent/50 text-fg" : "border-line text-muted"}`}>{k}</button>
        ))}
      </div>
      {tab === "ratios" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div><div className="mb-1 text-[11px] uppercase text-muted">Ratios (trailing 12 months)</div><KV obj={f.ratios} /></div>
          <div><div className="mb-1 text-[11px] uppercase text-muted">Key metrics (TTM)</div><KV obj={f.metrics} /></div>
        </div>
      )}
      {tab !== "ratios" && <Rows rows={f[tab]} />}
      {f.unavailable.length > 0 && <Note>Not returned: {f.unavailable.join(" · ")}</Note>}
    </Panel>
  );
}

function Options({ o }: { o: NonNullable<Analysis["options"]> }) {
  if ("unavailable" in o)
    return (
      <Panel title="Options positioning">
        <Note>{o.unavailable}</Note>
      </Panel>
    );
  return (
    <Panel title="Options positioning" right={<span className="num">{o.contracts} contracts</span>}>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <div><dt className="text-xs text-muted">Put/call volume</dt><dd className="num">{num(o.put_call_volume, 2)}</dd></div>
        <div><dt className="text-xs text-muted">Put/call open interest</dt><dd className="num">{num(o.put_call_oi, 2)}</dd></div>
        <div><dt className="text-xs text-muted">ATM implied vol (~30d)</dt><dd className="num">{pct(o.atm_iv_30d, 1)}</dd></div>
        <div><dt className="text-xs text-muted">Dealer gamma per 1% move</dt><dd className="num">{isNum(o.gamma_exposure_usd_per_1pct) ? `$${(o.gamma_exposure_usd_per_1pct / 1e9).toFixed(2)}B` : "—"}</dd></div>
      </dl>
      <StatusLine status={o.kind === "realtime" ? "LIVE" : "DELAYED"} asOf={o.as_of} />
      <Inspector rows={[["Kind", o.kind], ["As of", o.as_of], ["Method", o.method]]} />
    </Panel>
  );
}

function useSearch(q: string) {
  const [hits, setHits] = useState<Hit[]>([]);
  useEffect(() => {
    const term = q.trim();
    if (term.length < 1) return setHits([]);
    const ctl = new AbortController();
    const id = setTimeout(() => {
      api<{ results: Hit[] }>(`/search?q=${enc(term)}`, { signal: ctl.signal }).then((r) => setHits(r.results.slice(0, 20)), () => setHits([]));
    }, 250);
    return () => {
      clearTimeout(id);
      ctl.abort();
    };
  }, [q]);
  return hits;
}
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
  const syms = useApi<Symbols>("/symbols");
  const a = useApi<Analysis>(s ? `/analyze/${enc(s)}` : null);
  const chart = useApi<ChartData>(s ? `/chart/${enc(s)}` : null);
  const wl = useWatchlist();
  const [q, setQ] = useState(s);
  const hits = useSearch(q === s ? "" : q);
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
  const inWl = wl.includes(s);
  const ind = a.data?.live?.indicators ?? a.data?.indicators;

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
        <datalist id="symlist">
          {hits.map((x) => <option key={x.provider + x.symbol} value={x.symbol}>{x.name}{x.exchange && ` · ${x.exchange}`}</option>)}
          {!hits.length && syms.data?.universe.map((x) => <option key={x} value={x} />)}
        </datalist>
        <button className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white">Analyze</button>
        {s && (
          <button
            type="button"
            onClick={() => (inWl ? watchlist.remove(s) : watchlist.add(s))}
            title="Kept in this tab only; cleared when you close or refresh it"
            className="rounded-lg border border-line px-3 py-1.5 text-sm"
          >
            {inWl ? "★ Watching" : "☆ Watch (this tab)"}
          </button>
        )}
        {s && <span className="ml-auto"><LiveQuote symbol={s} initial={a.data?.quote} close={a.data && { value: a.data.indicators.close, change: null, as_of: a.data.as_of, status: "DELAYED", provider: a.data.provider, note: "daily close from the history provider" }} /></span>}
      </form>
      {wl.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">This tab&apos;s watchlist:</span>
          {wl.map((w) => (
            <Link key={w} href={`/analyze/?s=${enc(w)}`} className={`rounded-md border px-2 py-0.5 ${w === s ? "border-accent text-accent" : "border-line"}`}>{w}</Link>
          ))}
        </div>
      )}
      {!s && <Note>Enter any US-listed ticker. History, quotes and fundamentals are fetched live from the configured providers; nothing is stored.</Note>}
      {a.error && <ErrorBox error={a.error} onRetry={a.reload} />}
      {s && !a.data && !a.error && <Spinner label={`Analyzing ${s}`} />}
      {a.data && (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title={`${a.data.symbol} · ${a.data.name}`} right={<span className="num">closes through {a.data.as_of}</span>} className="lg:col-span-2">
              <div className="flex flex-wrap items-center gap-3">
                <span className={`text-2xl font-semibold ${toneText[STATE_TONE[a.data.state] ?? "muted"]}`}>{a.data.state_label}</span>
                <KindTag kind="heuristic" />
                {a.data.live && a.data.live.state !== a.data.state && (
                  <span className="text-sm">
                    intraday: <b className={toneText[STATE_TONE[a.data.live.state] ?? "muted"]}>{a.data.live.state_label}</b>
                  </span>
                )}
              </div>
              {a.data.profile && (
                <div className="mt-1 text-xs text-muted">
                  {[a.data.profile.exchange, a.data.profile.sector, a.data.profile.industry, a.data.profile.cap_bucket && `${a.data.profile.cap_bucket} cap`].filter(Boolean).join(" · ")}
                  {isNum(a.data.profile.market_cap) && ` · $${(a.data.profile.market_cap / 1e9).toFixed(1)}B`}
                </div>
              )}
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
                      Sector vs market 63d <span className={signedClass(a.data.sector.sector_rs63_vs_benchmark)}>{pct(a.data.sector.sector_rs63_vs_benchmark, 1, true)}</span> · stock vs sector{" "}
                      <span className={signedClass(a.data.sector.stock_rs63_vs_sector)}>{pct(a.data.sector.stock_rs63_vs_sector, 1, true)}</span>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-xs text-muted">Recent divergences</dt>
                  <dd className="space-y-0.5 text-xs">
                    {a.data.recent_divergences.length ? a.data.recent_divergences.slice(-3).map((d) => (
                      <div key={d.confirmed + d.type}><Badge tone={d.type === "bullish" ? "up" : "warn"}>{d.type}</Badge> confirmed {d.confirmed}</div>
                    )) : "none"}
                  </dd>
                </div>
              </dl>
              <Inspector
                rows={[
                  ["History provider", a.data.provider],
                  ["Authority", a.data.authority],
                  ["Adjustment", a.data.adjustment],
                  ["Confirmed through", a.data.as_of],
                  ["Intraday basis", a.data.live ? `${a.data.live.provider} · ${a.data.live.status} · ${a.data.live.as_of}` : "none (no live feed or market closed)"],
                  ["Market basis", a.data.market?.basis],
                ]}
              />
            </Panel>
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Panel title="Chart">{chart.error ? <ErrorBox error={chart.error} /> : chart.data ? <PriceChart data={chart.data} events={events} overlays={["sma20", "sma50", "sma200", "avwap_from_252d_low"]} liveSymbol={s} /> : <Spinner />}<Note>Overlays: 20/50/200-day averages and VWAP anchored at the 252-day low{chart.data?.avwap_anchor && ` (${chart.data.avwap_anchor})`}.{chart.data?.volume_profile && ` Volume profile (126d): POC ${num(chart.data.volume_profile.poc)}, value area ${num(chart.data.volume_profile.value_area_low)}–${num(chart.data.volume_profile.value_area_high)}.`}</Note></Panel>
            <Panel title="Indicators" right={a.data.live ? <span>provisional · {a.data.live.status}</span> : <span>confirmed close</span>}>
              <table className="w-full text-xs">
                <thead><tr><Th>Indicator</Th><Th className="text-right">Value</Th><Th className="text-right">Hist. pct</Th></tr></thead>
                <tbody>
                  {Object.entries(IND_LABELS).map(([k, [label, f]]) => (
                    <tr key={k}><Td>{label}</Td><Td className="text-right">{f(ind?.[k] ?? null)}</Td><Td className="text-right text-muted">{a.data!.percentiles[k] !== undefined ? pct(a.data!.percentiles[k], 0) : ""}</Td></tr>
                  ))}
                </tbody>
              </table>
              <Note>
                Hist. pct = where the last confirmed close sits in this symbol&apos;s own history.
                {a.data.live && ` Values include today's in-progress bar (${a.data.live.provider}, ${a.data.live.as_of}) and change until the close.`}
              </Note>
            </Panel>
          </div>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
            <Fundamentals f={a.data.fundamentals} />
            {a.data.options && <Options o={a.data.options} />}
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
