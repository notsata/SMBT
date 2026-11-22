"use client";

import { useEffect, useMemo, useState } from "react";
import { MARKER_TYPES, PriceChart } from "@/components/charts";
import { Badge, ErrorBox, Freshness, Gauge, KindTag, Note, Panel, Spinner, Td, Th, toneText } from "@/components/ui";
import type { Card, ChartData, Component, Overview, ProbRow, SignalEvent, SymbolInfo } from "@/lib/api";
import { REGIME_TONE, isNum, num, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const enc = encodeURIComponent;
const PREFERRED = ["SPY", "QQQ", "IWM", "DIA", "^GSPC", "^IXIC", "SMH"];

function useSymbol(def: string | null | undefined) {
  const [s, setS] = useState<string | null>(null);
  useEffect(() => {
    try {
      setS(localStorage.getItem("smbt-dash-symbol"));
    } catch {
      /* ignore */
    }
  }, []);
  const set = (v: string) => {
    setS(v);
    try {
      localStorage.setItem("smbt-dash-symbol", v);
    } catch {
      /* ignore */
    }
  };
  return [s ?? def ?? null, set] as const;
}

function Verdict({ ov }: { ov: Overview }) {
  const c = ov.cards;
  const b60 = ov.probabilities.bottom.find((p) => p.horizon === 60 && p.available);
  const t126 = ov.probabilities.top.find((p) => p.horizon === 126 && p.available);
  const bs = c.bottom.value as number | null;
  const ts = c.top.value as number | null;
  const tone = isNum(bs) && isNum(ts) ? (bs - ts >= 25 ? "up" : ts - bs >= 25 ? "down" : "muted") : "muted";
  const headline = tone === "up" ? "Conditions resemble historically attractive entry zones" : tone === "down" ? "Conditions resemble historically risky environments" : "No strong alignment between bottom and top evidence";
  const skill = (p?: ProbRow) => p?.oos?.brier_skill;
  return (
    <Panel title="Bottom line" right={<span className="num">{ov.symbol} · {ov.as_of}</span>}>
      <p className={`text-lg font-semibold ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : ""}`}>{headline}</p>
      <ul className="mt-2 space-y-1 text-sm text-fg/90">
        <li>
          Regime: <b>{c.regime.display}</b>. Bottom Opportunity <b className="num">{num(bs, 0)}</b>/100, Top Risk <b className="num">{num(ts, 0)}</b>/100 (heuristic scores, not probabilities).
        </li>
        {b60?.probability !== undefined && (
          <li>
            Past days in today&apos;s bottom-score bucket ({b60.bucket?.join("–")}) had a positive 60-session return <b className="num">{pct(b60.probability, 0)}</b> of the time vs a{" "}
            <span className="num">{pct(b60.base_rate, 0)}</span> base rate ({b60.episodes} episodes{!b60.sufficient && ", too few to rely on"}).{" "}
            {isNum(skill(b60)) && (
              <span className={(skill(b60) as number) > 0 ? "text-up" : "text-warn"}>
                Out of sample this bucket model {(skill(b60) as number) > 0 ? "beat" : "did not beat"} the base rate (Brier skill {num(skill(b60), 3)}).
              </span>
            )}
          </li>
        )}
        {t126?.probability !== undefined && (
          <li>
            A ≥10% decline within 126 sessions followed <b className="num">{pct(t126.probability, 0)}</b> of past days in today&apos;s top-risk bucket (base rate <span className="num">{pct(t126.base_rate, 0)}</span>,{" "}
            {t126.episodes} episodes).
            {isNum(skill(t126)) && <span className={(skill(t126) as number) > 0 ? "text-up" : "text-warn"}> OOS Brier skill {num(skill(t126), 3)}.</span>}
          </li>
        )}
      </ul>
      <Note>{ov.data_kind} Scores combine only indicators with data; coverage is shown on each card. See Validate for measured signal performance.</Note>
    </Panel>
  );
}

function ScoreCard({ id, card, active, onClick }: { id: string; card: Card; active: boolean; onClick: () => void }) {
  const invert = id === "top" || id === "macro";
  const isScore = ["bottom", "top", "trend", "macro", "sentiment"].includes(id);
  let main: React.ReactNode = <span className="text-base text-muted">Unavailable</span>;
  if (id === "regime" && card.value) main = <span className={toneText[REGIME_TONE[card.value as string] ?? "muted"]}>{card.display}</span>;
  else if (isNum(card.value)) main = <span className="num">{id === "breadth" ? `${num(card.value, 0)}%` : num(card.value, id === "vix" ? 2 : 0)}</span>;
  return (
    <button onClick={onClick} aria-expanded={active} className={`rounded-xl border bg-panel p-3 text-left transition-colors ${active ? "border-accent" : "border-line hover:border-muted/50"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted">{card.label}</span>
        {card.kind === "heuristic" && <span className="text-[10px] text-muted">heuristic</span>}
      </div>
      <div className={`mt-1 ${id === "regime" ? "text-base" : "text-2xl"} font-semibold`}>{main}</div>
      {isScore && <Gauge value={card.value as number | null} invert={invert} label={card.label} />}
      <div className="mt-1 text-[11px] text-muted">
        {id === "vix" && isNum(card.percentile) && <>pct {pct(card.percentile, 0)} · VIX/VIX3M {num(card.term_ratio, 2)}</>}
        {id === "trend" && card.direction && <>direction {card.direction}</>}
        {(id === "bottom" || id === "top") && isNum(card.coverage) && <>coverage {pct(card.coverage, 0)}</>}
        {id === "breadth" && !card.available && <>needs constituent data</>}
      </div>
      <Freshness asOf={card.as_of} stale={card.stale} />
    </button>
  );
}

function Components({ items, invert }: { items: Component[]; invert?: boolean }) {
  return (
    <ul className="space-y-1.5">
      {items.map((c) => (
        <li key={c.id} className="grid grid-cols-[1fr_auto] items-center gap-x-3 text-xs sm:grid-cols-[minmax(0,14rem)_1fr_3rem]">
          <span className={c.available ? "" : "text-muted line-through"} title={c.detail}>
            {c.label} <span className="text-muted">· {c.category}</span>
          </span>
          <div className="hidden h-1.5 rounded-full bg-panel2 sm:block">
            {isNum(c.value) && <div className={`h-full rounded-full ${invert ? "bg-down" : "bg-up"}`} style={{ width: `${c.value * 100}%`, opacity: 0.35 + c.value * 0.65 }} />}
          </div>
          <span className="num text-right">{c.available ? num(c.value, 2) : "n/a"}</span>
        </li>
      ))}
    </ul>
  );
}

function CardDetail({ id, ov }: { id: string; ov: Overview }) {
  const card = ov.cards[id as keyof Overview["cards"]];
  if (id === "bottom" || id === "top") {
    const ex = ov.explain[id];
    return (
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <KindTag kind="heuristic" /> Each component is 0–1 (point-in-time percentile or flag); categories are weighted by regime ({ex.regime}).
        </div>
        <Components items={ex.components} invert={id === "top"} />
      </div>
    );
  }
  if (id === "regime") return <ul className="list-disc space-y-1 pl-5 text-sm">{card.reasons?.map((r) => <li key={r}>{r}</li>)}</ul>;
  if (id === "trend")
    return (
      <ul className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
        {Object.entries(ov.explain.trend).map(([k, v]) => (
          <li key={k} className="rounded-lg bg-panel2 p-2">
            <div className="text-muted">{k.replace("_", " ")}</div>
            <div className="num text-base">{num(v, 2)}</div>
          </li>
        ))}
      </ul>
    );
  if (card.components)
    return (
      <ul className="grid gap-1 text-xs sm:grid-cols-2">
        {Object.entries(card.components).map(([k, v]) => (
          <li key={k} className="flex justify-between gap-3 border-b border-line/50 py-1">
            <span>{k}</span>
            <span className="num">{num(v, id === "macro" ? 2 : 0)}</span>
          </li>
        ))}
      </ul>
    );
  if (id === "breadth")
    return (
      <div className="text-sm">
        {card.available ? (
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>% above 200d: <b className="num">{num(card.pct_above_200, 0)}%</b></div>
            <div>McClellan: <b className="num">{num(card.mcclellan, 0)}</b></div>
            <div>Net new highs: <b className="num">{num(card.nhnl, 1)}%</b></div>
          </div>
        ) : null}
        <Note>{card.note}</Note>
      </div>
    );
  return <Note>Source: {card.source}</Note>;
}

function Probabilities({ ov }: { ov: Overview }) {
  const rows = [...ov.probabilities.bottom.map((r) => ({ ...r, side: "Bottom" })), ...ov.probabilities.top.map((r) => ({ ...r, side: "Top" }))];
  return (
    <Panel title="Historical outcomes for today's score buckets" right={<KindTag kind="empirical" />}>
      <div className="scroll-x">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <Th>Score</Th><Th>Event</Th><Th>Bucket</Th><Th>Frequency</Th><Th>Base rate</Th><Th>Episodes</Th><Th>95% CI</Th><Th>OOS Brier skill</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.side + r.event + r.horizon}>
                <Td>{r.side}</Td>
                <Td>{r.event === "positive_return" ? `Positive ${r.horizon}d return` : `≥10% drop within ${r.horizon}d`}</Td>
                <Td>{r.bucket?.join("–") ?? "—"}</Td>
                <Td className="font-semibold">{r.available ? pct(r.probability, 0) : "n/a"}</Td>
                <Td>{pct(r.base_rate, 0)}</Td>
                <Td>
                  {r.episodes ?? "—"} {r.available && !r.sufficient && <Badge tone="warn">thin</Badge>}
                </Td>
                <Td>{r.ci95 ? `${pct(r.ci95[0], 0)}–${pct(r.ci95[1], 0)}` : "—"}</Td>
                <Td className={signedClass(r.oos?.brier_skill)}>{num(r.oos?.brier_skill, 3)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Note>
        {ov.probabilities.method}. Episodes count independent clusters of qualifying days (overlapping windows are not independent). Positive Brier skill means the bucket estimate beat the
        unconditional base rate out of sample; negative means the score did not add predictive information for that event.
      </Note>
    </Panel>
  );
}

function SignalDetail({ e, onClose }: { e: SignalEvent; onClose: () => void }) {
  return (
    <div className="rounded-lg border border-accent/40 bg-panel2 p-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{e.label}</div>
          <div className="text-xs text-muted">
            {e.date}
            {e.regime && ` · regime ${e.regime}`}
          </div>
        </div>
        <button onClick={onClose} className="text-xs text-muted hover:text-fg" aria-label="Close signal detail">
          ✕
        </button>
      </div>
      <div className="mt-2 text-xs font-medium text-muted">Triggered by</div>
      <ul className="mt-1 space-y-0.5 text-xs">
        {e.triggered_by.map((t, i) => (
          <li key={i} className="flex justify-between gap-3">
            <span>{t.label}{t.category && <span className="text-muted"> · {t.category}</span>}</span>
            <span className="num">{Array.isArray(t.value) ? t.value.map((v) => (typeof v === "number" ? num(v, 2) : String(v))).join(" → ") : typeof t.value === "number" ? num(t.value, 2) : ""}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2 text-xs font-medium text-muted">What happened next (evaluation only)</div>
      <div className="mt-1 flex gap-4 text-xs">
        {Object.entries(e.forward_realized).map(([h, v]) => (
          <span key={h} className={signedClass(v)}>
            {h}d {pct(v, 1, true)}
          </span>
        ))}
      </div>
    </div>
  );
}

const LEGEND: Record<string, string> = {
  bottom: "B Bottom ≥70",
  top: "T Top risk ≥70",
  bullish_divergence: "● Bull div.",
  bearish_divergence: "● Bear div.",
  zweig_thrust: "Z Zweig",
  regime_bear_capitulation: "C Capitulation",
  regime_distribution: "D Distribution",
};

export default function Dashboard() {
  const syms = useApi<{ symbols: SymbolInfo[]; default: string | null }>("/symbols");
  const [symbol, setSymbol] = useSymbol(syms.data?.default);
  const ov = useApi<Overview>(symbol ? `/overview?symbol=${enc(symbol)}` : null);
  const chart = useApi<ChartData>(symbol ? `/chart/${enc(symbol)}` : null);
  const sig = useApi<{ events: SignalEvent[] }>(symbol ? `/signals/${enc(symbol)}` : null);
  const [open, setOpen] = useState<string | null>(null);
  const [sel, setSel] = useState<SignalEvent | null>(null);
  const [types, setTypes] = useState<string[]>(["bottom", "top", "zweig_thrust", "regime_bear_capitulation", "regime_distribution"]);
  const choices = useMemo(() => {
    const all = syms.data?.symbols ?? [];
    const pref = PREFERRED.filter((p) => all.some((s) => s.symbol === p));
    return { pref, other: all.filter((s) => !pref.includes(s.symbol) && s.kind !== "stock").map((s) => s.symbol) };
  }, [syms.data]);

  if (syms.error) return <ErrorBox error={syms.error} onRetry={syms.reload} />;
  if (syms.data && !syms.data.default)
    return (
      <Panel title="No market data yet">
        <p className="text-sm">The database is empty. Open Settings &amp; Data to run ingestion or load the bundled sample data.</p>
      </Panel>
    );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Market dashboard</h1>
        {choices.pref.map((s) => (
          <button key={s} onClick={() => setSymbol(s)} className={`rounded-lg border px-2.5 py-1 text-xs font-medium ${s === symbol ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}>
            {s}
          </button>
        ))}
        {choices.other.length > 0 && (
          <select aria-label="Other symbols" value={choices.other.includes(symbol ?? "") ? (symbol as string) : ""} onChange={(e) => e.target.value && setSymbol(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1 text-xs">
            <option value="">More…</option>
            {choices.other.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        )}
        {ov.data && (
          <span className="ml-auto text-sm">
            <span className="num font-semibold">{num(ov.data.price.close)}</span> <span className={`num ${signedClass(ov.data.price.change_1d)}`}>{pct(ov.data.price.change_1d, 2, true)}</span>
            <span className="ml-2 text-xs text-muted">close {ov.data.as_of}</span>
            {ov.data.price.quote && (
              <span className="ml-2 text-xs text-muted" title={ov.data.price.quote.source}>
                · last {num(ov.data.price.quote.price)} ({ov.data.price.quote.delay})
              </span>
            )}
          </span>
        )}
      </div>

      {ov.error && <ErrorBox error={ov.error} onRetry={ov.reload} />}
      {!ov.data && !ov.error && <Spinner label="Computing market state" />}
      {ov.data && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
            {(["regime", "bottom", "top", "trend", "vix", "breadth", "sentiment", "macro"] as const).map((id) => (
              <ScoreCard key={id} id={id} card={ov.data!.cards[id]} active={open === id} onClick={() => setOpen(open === id ? null : id)} />
            ))}
          </div>
          {open && (
            <Panel title={ov.data.cards[open as keyof Overview["cards"]].label} right={<button onClick={() => setOpen(null)}>Close</button>}>
              <CardDetail id={open} ov={ov.data} />
            </Panel>
          )}
          <Verdict ov={ov.data} />
        </>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel
          title={`${symbol ?? ""} price with historical signals`}
          right={chart.data && <span className="hidden sm:inline">{chart.data.source.split("(")[0]}</span>}
        >
          <div className="mb-2 flex flex-wrap gap-1.5">
            {MARKER_TYPES.map((t) => (
              <button
                key={t}
                aria-pressed={types.includes(t)}
                onClick={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])}
                className={`rounded-md border px-2 py-0.5 text-[11px] ${types.includes(t) ? "border-accent/50 text-fg" : "border-line text-muted"}`}
              >
                {LEGEND[t]}
              </button>
            ))}
          </div>
          {chart.error && <ErrorBox error={chart.error} />}
          {chart.data ? <PriceChart data={chart.data} events={sig.data?.events} visibleTypes={types} onSelect={setSel} /> : !chart.error && <Spinner label="Loading chart" />}
          <Note>Tap a marker for the indicators that triggered it. Pinch or scroll to zoom. Signals use only data available on each date.</Note>
          {sel && <SignalDetail e={sel} onClose={() => setSel(null)} />}
        </Panel>
        {ov.data && (
          <Panel title="Top risk drivers" right={<KindTag kind="heuristic" />}>
            <Components items={ov.data.explain.top.components.filter((c) => c.available).slice(0, 8)} invert />
            <div className="mt-4 mb-2 text-sm font-semibold">Bottom opportunity drivers</div>
            <Components items={ov.data.explain.bottom.components.filter((c) => c.available).slice(0, 8)} />
          </Panel>
        )}
      </div>

      {ov.data && <Probabilities ov={ov.data} />}
      {ov.data && (
        <Panel title="Not available (requires licensed data)">
          <ul className="grid gap-1 text-xs text-muted sm:grid-cols-2">
            {Object.entries(ov.data.unavailable).map(([k, v]) => (
              <li key={k}>
                <b className="text-fg/80">{k.replaceAll("_", " ")}</b>: {v}
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
