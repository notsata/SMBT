"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge, ErrorBox, Note, Panel, Segmented, Spinner, Td, Th } from "@/components/ui";
import type { Scan } from "@/lib/api";
import { num, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function ScannerPage() {
  const [direction, setDirection] = useState<"bullish" | "bearish">("bullish");
  const [timeframe, setTimeframe] = useState<"daily" | "weekly">("daily");
  const [kind, setKind] = useState("");
  const [sector, setSector] = useState("");
  const [cap, setCap] = useState("");
  const [signal, setSignal] = useState("");
  const [q, setQ] = useState("");
  const qs = new URLSearchParams({ direction, timeframe, kind, sector, cap, signal, q }).toString();
  const { data, error, loading, reload } = useApi<Scan>(`/scanner?${qs}`);
  const sel = "rounded-lg border border-line bg-panel px-2 py-1.5 text-xs";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Opportunity scanner</h1>
        <Segmented label="Direction" value={direction} onChange={setDirection} options={[{ value: "bullish", label: "Bullish setups" }, { value: "bearish", label: "Risk setups" }]} />
        <Segmented label="Timeframe" value={timeframe} onChange={setTimeframe} options={[{ value: "daily", label: "Daily" }, { value: "weekly", label: "Weekly" }]} />
      </div>
      <div className="flex flex-wrap gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ticker / name" aria-label="Filter by ticker" className={sel + " w-36"} />
        <select aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value)} className={sel}>
          <option value="">All types</option><option value="etf">ETFs</option><option value="stock">Stocks</option><option value="index">Indexes</option>
        </select>
        <select aria-label="Market cap" value={cap} onChange={(e) => setCap(e.target.value)} className={sel}>
          <option value="">All caps</option><option value="mega">Mega</option><option value="large">Large</option><option value="mid">Mid</option><option value="small">Small</option>
        </select>
        <select aria-label="Sector" value={sector} onChange={(e) => setSector(e.target.value)} className={sel}>
          <option value="">All sectors</option>
          {data?.sectors.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select aria-label="Signal type" value={signal} onChange={(e) => setSignal(e.target.value)} className={sel}>
          <option value="">All signals</option>
          {data && Object.entries(data.flags).filter(([, v]) => v.direction === direction).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data && <Spinner label="Scanning universe" />}
      {data && (
        <>
          <Panel title={`${data.total} symbols`} right={loading ? "updating…" : `ranked by confluence, then pooled ${data.horizon}-bar evidence`}>
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead>
                  <tr><Th>Symbol</Th><Th>Setups</Th><Th>Confluence</Th><Th>Evidence</Th><Th>RSI</Th><Th>vs 200d</Th><Th>Drawdown</Th><Th>RS 63d</Th><Th>Vol×</Th><Th>As of</Th></tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.symbol} className="hover:bg-panel2">
                      <Td>
                        <Link href={`/analyze/?s=${encodeURIComponent(r.symbol)}`} className="font-semibold text-accent">{r.symbol}</Link>
                        <div className="max-w-[12rem] truncate text-[10px] text-muted">{r.name}</div>
                      </Td>
                      <Td className="whitespace-normal">
                        <div className="flex min-w-[10rem] flex-wrap gap-1">
                          {r.flags.length ? r.flags.map((f) => <Badge key={f} tone={data.flags[f].direction === "bullish" ? "up" : "down"}>{data.flags[f].label}</Badge>) : <span className="text-muted">—</span>}
                        </div>
                      </Td>
                      <Td className="font-semibold">{r[`${direction}_confluence`]}</Td>
                      <Td>{num(r[`${direction}_evidence`], 2)}</Td>
                      <Td>{num(r.rsi14, 0)}</Td>
                      <Td className={signedClass(r.dist200)}>{pct(r.dist200, 1, true)}</Td>
                      <Td>{pct(r.drawdown, 0)}</Td>
                      <Td className={signedClass(r.rs63)}>{pct(r.rs63, 1, true)}</Td>
                      <Td>{num(r.vol_ratio, 1)}</Td>
                      <Td className="text-muted">{r.as_of}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title="Historical evidence per setup (pooled across the universe)">
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Setup</Th><Th>Side</Th><Th>Past onsets</Th><Th>Mean excess {data.horizon}-bar return</Th><Th>Share beating symbol average</Th><Th>t-stat</Th></tr></thead>
                <tbody>
                  {Object.entries(data.flag_evidence).map(([k, v]) => (
                    <tr key={k}>
                      <Td>{v.label}</Td><Td>{v.direction}</Td><Td>{v.n}</Td>
                      <Td className={signedClass(v.excess_mean, v.direction === "bearish")}>{pct(v.excess_mean, 2, true)}</Td>
                      <Td>{pct(v.excess_hit_rate, 0)}</Td><Td>{num(v.t_stat, 2)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Note>{data.note} Evidence column = sum of mean excess returns (in %) of active setups whose pooled history pointed the right way with ≥30 onsets.</Note>
          </Panel>
        </>
      )}
    </div>
  );
}
