"use client";

import { useMemo, useState } from "react";
import { LineChart } from "@/components/charts";
import { Badge, DistBar, ErrorBox, Note, Panel, Spinner, Td, Th } from "@/components/ui";
import { api, type Backtest, type Metrics, type ScoreRow, type SignalReport, type Strategy, type Symbols } from "@/lib/api";
import { HORIZONS, isNum, num, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const enc = encodeURIComponent;

function MetricsTable({ cols }: { cols: [string, Metrics | undefined][] }) {
  const rows: [string, (m: Metrics) => string][] = [
    ["CAGR", (m) => pct(m.cagr, 1)],
    ["Total return", (m) => pct(m.total_return, 0)],
    ["Volatility", (m) => pct(m.volatility, 1)],
    ["Sharpe", (m) => num(m.sharpe, 2)],
    ["Sortino", (m) => num(m.sortino, 2)],
    ["Max drawdown", (m) => pct(m.max_drawdown, 1)],
    ["Calmar", (m) => num(m.calmar, 2)],
    ["Exposure", (m) => (isNum(m.exposure) ? pct(m.exposure, 0) : "100%")],
    ["Trades", (m) => (isNum(m.trades) ? String(m.trades) : "—")],
    ["Win rate", (m) => pct(m.win_rate, 0)],
    ["Avg trade", (m) => pct(m.avg_trade, 2, true)],
  ];
  return (
    <div className="scroll-x">
      <table className="w-full text-xs">
        <thead><tr><Th />{cols.map(([n]) => <Th key={n} className="text-right">{n}</Th>)}</tr></thead>
        <tbody>
          {rows.map(([label, f]) => (
            <tr key={label}><Td className="text-muted">{label}</Td>{cols.map(([n, m]) => <Td key={n} className="text-right">{m ? f(m) : "—"}</Td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacktestPanel({ symbol }: { symbol: string }) {
  const strategies = useApi<Strategy[]>("/strategies");
  const [strategy, setStrategy] = useState("score_overlay");
  const [train, setTrain] = useState(6);
  const [test, setTest] = useState(2);
  const [comm, setComm] = useState(1);
  const [slip, setSlip] = useState(2);
  const [res, setRes] = useState<Backtest | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<Backtest>("/backtest", { method: "POST", json: { symbol, strategy, train_years: train, test_years: test, commission_bps: comm, slippage_bps: slip, cv: true } }));
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const eq = useMemo(() => (res?.equity ? { t: res.equity.map((e) => e.t), lines: [{ name: res.name, values: res.equity.map((e) => e.equity) }, { name: "Buy & hold", values: res.equity.map((e) => e.bench_equity) }] } : null), [res]);
  const strat = strategies.data?.find((s) => s.id === strategy);
  const inp = "w-16 rounded-lg border border-line bg-panel px-2 py-1 text-xs num";
  const d = res?.oos_vs_benchmark;
  return (
    <Panel title="Walk-forward strategy test" right="strict out-of-sample">
      <div className="flex flex-wrap items-end gap-3 text-xs">
        <label className="flex flex-col gap-1">Strategy
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1">
            {strategies.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Train yrs<input type="number" min={2} max={20} value={train} onChange={(e) => setTrain(+e.target.value)} className={inp} /></label>
        <label className="flex flex-col gap-1">Test yrs<input type="number" min={1} max={5} value={test} onChange={(e) => setTest(+e.target.value)} className={inp} /></label>
        <label className="flex flex-col gap-1">Commission bps<input type="number" min={0} step={0.5} value={comm} onChange={(e) => setComm(+e.target.value)} className={inp} /></label>
        <label className="flex flex-col gap-1">Slippage bps<input type="number" min={0} step={0.5} value={slip} onChange={(e) => setSlip(+e.target.value)} className={inp} /></label>
        <button onClick={run} disabled={busy} className="rounded-lg bg-accent px-4 py-1.5 font-medium text-white disabled:opacity-50">{busy ? "Running…" : "Run"}</button>
      </div>
      {strat && <Note>{strat.description} Parameter grid: {Object.entries(strat.grid).map(([k, v]) => `${k} ∈ {${v.join(", ")}}`).join("; ") || "none"}.</Note>}
      {err && <ErrorBox error={err} />}
      {res?.error && <ErrorBox error={new Error(res.error)} />}
      {res && !res.error && (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <b>{res.name}</b> on {res.symbol}, OOS {res.oos_period[0]} → {res.oos_period[1]}
            {d?.sharpe_diff_ci95 && (
              <Badge tone={d.sharpe_diff_ci95[0] > 0 ? "up" : d.sharpe_diff_ci95[1] < 0 ? "down" : "muted"}>
                ΔSharpe {num(d.sharpe_diff, 2)} [95% CI {num(d.sharpe_diff_ci95[0], 2)}, {num(d.sharpe_diff_ci95[1], 2)}]
              </Badge>
            )}
            {d?.sharpe_diff_ci95 && <span className="text-xs text-muted">{d.sharpe_diff_ci95[0] > 0 ? "Statistically better risk-adjusted than buy & hold." : "Not statistically distinguishable from (or worse than) buy & hold."}</span>}
          </div>
          {eq && <LineChart t={eq.t} lines={eq.lines} height={260} />}
          <div className="grid gap-4 lg:grid-cols-2">
            <MetricsTable cols={[["Strategy (OOS)", res.oos], [`${res.symbol} buy & hold`, res.oos_benchmark], ...Object.entries(res.other_benchmarks ?? {})]} />
            <div className="space-y-3 text-xs">
              <div>
                <div className="mb-1 font-semibold">By market state (benchmark above/below 200-day)</div>
                <table className="w-full"><thead><tr><Th>State</Th><Th>Days</Th><Th>Ann. return</Th><Th>B&amp;H</Th><Th>Sharpe</Th><Th>B&amp;H</Th></tr></thead>
                  <tbody>{Object.entries(res.oos_by_market_state).map(([k, v]) => (
                    <tr key={k}><Td>{k}</Td><Td>{v.n_days}</Td><Td className={signedClass(v.ann_return)}>{pct(v.ann_return, 1)}</Td><Td>{pct(v.bench_ann_return, 1)}</Td><Td>{num(v.sharpe, 2)}</Td><Td>{num(v.bench_sharpe, 2)}</Td></tr>
                  ))}</tbody></table>
              </div>
              <div className="rounded-lg border border-warn/30 bg-warn/10 p-2 text-warn">
                In-sample with defaults ({Object.entries(res.in_sample_defaults.params).map(([k, v]) => `${k}=${v}`).join(", ") || "—"}): CAGR {pct(res.in_sample_defaults.metrics.cagr, 1)}, Sharpe {num(res.in_sample_defaults.metrics.sharpe, 2)}. {res.in_sample_defaults.warning}
              </div>
              <div className="text-muted">
                Assumptions: execution at {res.assumptions.execution}, {res.assumptions.commission_bps}+{res.assumptions.slippage_bps} bps per side, cash yield {res.assumptions.cash_yield}, objective {res.assumptions.objective}. {res.data.survivorship} Data: {res.data.provider} ({res.data.adjustment}) {res.data.first} → {res.data.last}, {res.data.basis}.
              </div>
            </div>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold">Walk-forward folds (parameters chosen on train only)</div>
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Train</Th><Th>Test</Th><Th>Chosen</Th><Th>Train Sharpe</Th><Th>Test CAGR</Th><Th>B&amp;H CAGR</Th><Th>Test Sharpe</Th><Th>B&amp;H Sharpe</Th><Th>Test MaxDD</Th></tr></thead>
                <tbody>{res.folds.map((f) => (
                  <tr key={f.test[0]}><Td>{f.train[0].slice(0, 4)}–{f.train[1].slice(0, 4)}</Td><Td>{f.test[0]} → {f.test[1]}</Td><Td>{Object.entries(f.chosen_params).map(([k, v]) => `${k}=${v}`).join(" ") || "—"}</Td>
                    <Td>{num(f.train_sharpe, 2)}</Td><Td className={signedClass(f.test_metrics.cagr)}>{pct(f.test_metrics.cagr, 1)}</Td><Td>{pct(f.test_benchmark.cagr, 1)}</Td><Td>{num(f.test_metrics.sharpe, 2)}</Td><Td>{num(f.test_benchmark.sharpe, 2)}</Td><Td>{pct(f.test_metrics.max_drawdown, 1)}</Td></tr>
                ))}</tbody>
              </table>
            </div>
          </div>
          {res.purged_cv && !res.purged_cv.error && (
            <div>
              <div className="mb-1 text-xs font-semibold">Purged time-series cross-validation — beat buy &amp; hold Sharpe in {pct(res.purged_cv.share_folds_beating_benchmark, 0)} of eras</div>
              <div className="scroll-x">
                <table className="w-full text-xs">
                  <thead><tr><Th>Test era</Th><Th>Chosen</Th><Th>Sharpe</Th><Th>B&amp;H Sharpe</Th><Th>CAGR</Th><Th>B&amp;H CAGR</Th></tr></thead>
                  <tbody>{res.purged_cv.folds.map((f) => (
                    <tr key={f.test[0]}><Td>{f.test[0]} → {f.test[1]}</Td><Td>{Object.entries(f.chosen_params).map(([k, v]) => `${k}=${v}`).join(" ") || "—"}</Td><Td className={f.beat_benchmark_sharpe ? "text-up" : "text-down"}>{num(f.test_metrics.sharpe, 2)}</Td><Td>{num(f.benchmark.sharpe, 2)}</Td><Td>{pct(f.test_metrics.cagr, 1)}</Td><Td>{pct(f.benchmark.cagr, 1)}</Td></tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

function SignalPanel({ symbol }: { symbol: string }) {
  const board = useApi<{ rows: ScoreRow[]; note: string; horizon: number }>(`/scoreboard/${enc(symbol)}`);
  const [signal, setSignal] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(70);
  const rep = useApi<SignalReport>(signal ? `/signal-stats/${enc(symbol)}?signal=${signal}&threshold=${threshold}` : null);
  return (
    <Panel title="Signal evidence (event studies)" right="entry next open · vs random entries">
      {board.error && <ErrorBox error={board.error} />}
      {!board.data && !board.error && <Spinner label="Measuring signals" />}
      {board.data && (
        <div className="scroll-x">
          <table className="w-full text-xs">
            <thead><tr><Th>Signal</Th><Th>Side</Th><Th>Events</Th><Th>Median {board.data.horizon}d</Th><Th>Hit rate</Th><Th>Base</Th><Th>Excess mean</Th><Th>p</Th><Th>q (FDR)</Th><Th>Now</Th></tr></thead>
            <tbody>
              {board.data.rows.map((r) => (
                <tr key={r.signal} onClick={() => setSignal(r.signal)} className={`cursor-pointer hover:bg-panel2 ${signal === r.signal ? "bg-panel2" : ""}`}>
                  <Td className="text-accent">{r.label}</Td><Td>{r.direction}</Td><Td>{r.n_events}</Td><Td className={signedClass(r.median)}>{pct(r.median, 1, true)}</Td>
                  <Td>{pct(r.hit_rate, 0)}</Td><Td className="text-muted">{pct(r.baseline_hit_rate, 0)}</Td><Td className={signedClass(r.excess_mean, r.direction === "bearish")}>{pct(r.excess_mean, 2, true)}</Td>
                  <Td>{num(r.p_value, 3)}</Td>
                  <Td>{num(r.q_value, 3)} {r.significant_after_fdr ? <Badge tone="up">robust</Badge> : r.significant ? <Badge tone="warn">raw only</Badge> : null}</Td>
                  <Td>{r.active_now ? <Badge tone="accent">active</Badge> : ""}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          <Note>{board.data.note} Bearish signals succeed when forward returns are below the baseline. Small event counts (&lt; 20) are anecdotal regardless of p-value.</Note>
        </div>
      )}
      {signal && (
        <div className="mt-4 border-t border-line pt-4">
          {(signal === "bottom_score" || signal === "top_score") && (
            <label className="mb-2 flex items-center gap-2 text-xs">Threshold
              <input type="range" min={50} max={90} step={5} value={threshold} onChange={(e) => setThreshold(+e.target.value)} />
              <span className="num">{threshold}</span>
            </label>
          )}
          {rep.error && <ErrorBox error={rep.error} />}
          {!rep.data && !rep.error && <Spinner />}
          {rep.data && (
            <div className="space-y-4">
              <div className="text-sm font-semibold">{rep.data.label} — {rep.data.event_study.n_events} events</div>
              <div className="scroll-x">
                <table className="w-full text-xs">
                  <thead><tr><Th>Horizon</Th><Th>n</Th><Th>Median</Th><Th>Hit</Th><Th>Base hit</Th><Th className="min-w-[160px]">Distribution (5–95%, box 25–75%, ▏baseline median)</Th><Th>Excess mean</Th><Th>Median MAE</Th><Th>p vs random</Th></tr></thead>
                  <tbody>
                    {HORIZONS.map((h) => {
                      const s = rep.data!.event_study.horizons[h];
                      if (!s) return null;
                      return (
                        <tr key={h}><Td>{h}d</Td><Td>{s.n}</Td><Td className={signedClass(s.median)}>{pct(s.median, 1, true)}</Td><Td>{pct(s.hit_rate, 0)}</Td><Td className="text-muted">{pct(s.baseline?.hit_rate, 0)}</Td>
                          <Td><DistBar p={s.pct} baselineMedian={s.baseline?.median} domain={+h >= 126 ? 0.5 : +h >= 60 ? 0.3 : 0.15} /></Td>
                          <Td className={signedClass(s.excess_mean, rep.data!.direction === "bearish")}>{pct(s.excess_mean, 2, true)}</Td><Td>{pct(s.mae_median, 1)}</Td>
                          <Td>{num(s.p_value_vs_random, 3)} {s.significant_5pct && <Badge tone="up">p&lt;.05</Badge>}</Td></tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <div className="text-xs">
                  <div className="mb-1 font-semibold">Turning-point accuracy (±{rep.data.turning_points.window} sessions of a {pct(rep.data.turning_points.pivot_threshold, 0)} swing {rep.data.direction === "bullish" ? "low" : "high"})</div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-lg bg-panel2 p-2"><div className="text-muted">Precision</div><div className="num text-base">{pct(rep.data.turning_points.precision, 0)}</div><div className="text-muted">{rep.data.turning_points.false_positives ?? 0} false positives</div></div>
                    <div className="rounded-lg bg-panel2 p-2"><div className="text-muted">Recall</div><div className="num text-base">{pct(rep.data.turning_points.recall, 0)}</div><div className="text-muted">{rep.data.turning_points.missed_pivots ?? 0} of {rep.data.turning_points.pivots} missed</div></div>
                    <div className="rounded-lg bg-panel2 p-2"><div className="text-muted">Median lag</div><div className="num text-base">{num(rep.data.turning_points.median_lag_sessions, 1)}</div><div className="text-muted">sessions (− = early)</div></div>
                  </div>
                  <Note>{rep.data.turning_points.note}</Note>
                </div>
                <div className="text-xs">
                  <div className="mb-1 font-semibold">Stability by era (60d excess)</div>
                  <table className="w-full"><thead><tr><Th>Era</Th><Th>Events</Th><Th>Excess</Th><Th>Hit</Th><Th>Base</Th></tr></thead>
                    <tbody>{rep.data.stability_by_era.map((e) => (
                      <tr key={e.period[0]}><Td>{e.period[0].slice(0, 7)} → {e.period[1].slice(0, 7)}</Td><Td>{e.n_events}</Td><Td className={signedClass(e.excess_mean, rep.data!.direction === "bearish")}>{pct(e.excess_mean, 1, true)}</Td><Td>{pct(e.hit_rate, 0)}</Td><Td>{pct(e.baseline_hit_rate, 0)}</Td></tr>
                    ))}</tbody></table>
                </div>
              </div>
              <Note>{rep.data.method}</Note>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

export default function ValidatePage() {
  const syms = useApi<Symbols>("/symbols");
  const [symbol, setSymbol] = useState<string | null>(null);
  const sym = symbol ?? syms.data?.default ?? null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Validation &amp; backtesting</h1>
        <select aria-label="Symbol" value={sym ?? ""} onChange={(e) => setSymbol(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1 text-xs">
          {syms.data && [...new Set([...syms.data.benchmarks, ...syms.data.universe])].map((s) => <option key={s}>{s}</option>)}
        </select>
        <span className="text-xs text-muted">Every number here is measured on stored history; nothing is simulated or assumed.</span>
      </div>
      {sym && <BacktestPanel key={`bt-${sym}`} symbol={sym} />}
      {sym && <SignalPanel key={`sp-${sym}`} symbol={sym} />}
    </div>
  );
}
