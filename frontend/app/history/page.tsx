"use client";

import { useState } from "react";
import { DistBar, ErrorBox, KindTag, Note, Panel, Spinner, Td, Th } from "@/components/ui";
import type { Analogs, SymbolInfo } from "@/lib/api";
import { HORIZONS, pct, signedClass } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function HistoryPage() {
  const syms = useApi<{ symbols: SymbolInfo[]; default: string | null }>("/symbols");
  const [symbol, setSymbol] = useState<string | null>(null);
  const sym = symbol ?? syms.data?.default ?? null;
  const { data, error, reload } = useApi<Analogs>(sym ? `/analogs?symbol=${encodeURIComponent(sym)}&k=12` : null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-2 text-lg font-semibold">Historical comparison</h1>
        <select aria-label="Symbol" value={sym ?? ""} onChange={(e) => setSymbol(e.target.value)} className="rounded-lg border border-line bg-panel px-2 py-1 text-xs">
          {syms.data?.symbols.filter((s) => s.kind !== "stock").map((s) => <option key={s.symbol}>{s.symbol}</option>)}
        </select>
        <KindTag kind="similarity" />
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {!data && !error && <Spinner label="Finding analogs" />}
      {data && (
        <>
          <Panel title="Closest historical periods" right={data.as_of && `as of ${data.as_of}`}>
            {data.matches.length === 0 ? (
              <p className="text-sm text-muted">{data.note}</p>
            ) : (
              <div className="scroll-x">
                <table className="w-full text-xs">
                  <thead><tr><Th>Date</Th><Th>Similarity</Th><Th>Event</Th>{HORIZONS.map((h) => <Th key={h} className="text-right">{h}d</Th>)}<Th className="text-right">Max DD 126d</Th></tr></thead>
                  <tbody>
                    {data.matches.map((m) => (
                      <tr key={m.date}>
                        <Td>{m.date}</Td><Td>{m.similarity.toFixed(3)}</Td><Td className="whitespace-normal">{m.event ?? <span className="text-muted">—</span>}</Td>
                        {HORIZONS.map((h) => <Td key={h} className={`text-right ${signedClass(m.forward[h])}`}>{pct(m.forward[h], 1, true)}</Td>)}
                        <Td className="text-right text-down">{pct(m.forward.max_drawdown_126, 1)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Note>
              Features compared ({data.features_used?.length ?? 0}): {data.features_used?.join(", ")}. {data.note}
            </Note>
          </Panel>
          {data.outcome_distribution && (
            <Panel title="What followed the analogs vs all days">
              <div className="scroll-x">
                <table className="w-full text-xs">
                  <thead><tr><Th>Horizon</Th><Th>Analogs (n)</Th><Th>Median</Th><Th>Hit rate</Th><Th className="min-w-[160px]">Analog distribution (▏all-days median)</Th><Th>All days median</Th><Th>All days hit</Th></tr></thead>
                  <tbody>
                    {HORIZONS.map((h) => {
                      const a = data.outcome_distribution![h];
                      const u = data.unconditional?.[h];
                      return (
                        <tr key={h}>
                          <Td>{h}d</Td><Td>{a?.n ?? 0}</Td><Td className={signedClass(a?.median)}>{pct(a?.median, 1, true)}</Td><Td>{pct(a?.hit_rate, 0)}</Td>
                          <Td><DistBar p={a?.pct} baselineMedian={u?.median} domain={+h >= 126 ? 0.5 : +h >= 60 ? 0.3 : 0.15} /></Td>
                          <Td>{pct(u?.median, 1, true)}</Td><Td>{pct(u?.hit_rate, 0)}</Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Note>Distributions, not averages: the box spans the middle half of outcomes; whiskers 5th–95th percentile.</Note>
            </Panel>
          )}
          <Panel title="Similarity to major turning points">
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Date</Th><Th>Event</Th><Th>Type</Th><Th>Similarity</Th><Th>Features</Th><Th className="text-right">60d after</Th><Th className="text-right">252d after</Th></tr></thead>
                <tbody>
                  {data.key_dates.map((k) => (
                    <tr key={k.date}>
                      <Td>{k.date}</Td><Td>{k.name}</Td><Td>{k.kind}</Td><Td className="font-semibold">{k.similarity.toFixed(3)}</Td><Td>{k.features_compared}</Td>
                      <Td className={`text-right ${signedClass(k.forward["60"])}`}>{pct(k.forward["60"], 1, true)}</Td><Td className={`text-right ${signedClass(k.forward["252"])}`}>{pct(k.forward["252"], 1, true)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Note>Only turning points inside the loaded history appear (e.g. COVID and 2022 need data through those years).</Note>
          </Panel>
        </>
      )}
    </div>
  );
}
