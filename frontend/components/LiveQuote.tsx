"use client";

import type { DataStatus, Quote } from "@/lib/api";
import { isNum, num, pct, signedClass } from "@/lib/format";
import { useQuote } from "@/lib/live";
import { Inspector, StatusBadge } from "./ui";

export type ConfirmedClose = { value: number | null; change: number | null; as_of: string; status: DataStatus | string; provider?: string | null; note?: string | null };

/** Streaming last price with its status and provenance; falls back to the last confirmed close, labelled as such. */
export function LiveQuote({ symbol, initial, close }: { symbol: string; initial?: Quote | null; close?: ConfirmedClose }) {
  const q = useQuote(symbol) ?? initial ?? null;
  const usable = q && isNum(q.last) && q.status !== "UNAVAILABLE";
  if (!usable)
    return (
      <span className="flex flex-wrap items-center gap-1.5 text-sm">
        {close && (
          <>
            <span className="num font-semibold">{num(close.value)}</span>
            <span className={`num ${signedClass(close.change)}`}>{pct(close.change, 2, true)}</span>
            <span className="text-xs text-muted">close {close.as_of}</span>
          </>
        )}
        <StatusBadge status={close ? close.status : "UNAVAILABLE"} reason={q?.reason ?? close?.note ?? "no live feed for this symbol"} />
        <Inspector rows={[["Live feed", q?.reason ?? "not streaming"], ["Shown value", close ? "last confirmed daily close" : "none"], ["Close provider", close?.provider], ["Note", close?.note]]} />
      </span>
    );
  const ext = q.session && q.session !== "regular" && isNum(q.ext_last) && q.ext_last !== q.last;
  return (
    <span data-testid="live-quote" data-status={q.status} className="flex flex-wrap items-center gap-1.5 text-sm">
      <span data-testid="live-last" className="num font-semibold">{num(q.last)}</span>
      <span className={`num ${signedClass(q.change)}`}>
        {isNum(q.change) ? (q.change > 0 ? "+" : "") + num(q.change) : "—"} ({pct(q.change_pct, 2, true)})
      </span>
      {ext && <span className="num text-xs text-muted">{q.session === "premarket" ? "pre" : "after hours"} {num(q.ext_last)}</span>}
      <StatusBadge status={q.status} reason={q.reason} />
      <Inspector
        rows={[
          ["Provider", q.provider],
          ["Feed", q.feed],
          ["Authority", q.authority],
          ["Kind", q.kind],
          ["Known delay", isNum(q.delay_s) ? (q.delay_s ? `${Math.round(q.delay_s / 60)} min` : "none (real time)") : undefined],
          ["Session", q.session],
          ["Last trade (event time)", q.last_ts],
          ["Received", q.recv_ts],
          ["Bid / ask", isNum(q.bid) || isNum(q.ask) ? `${num(q.bid)} × ${q.bid_size ?? "—"} / ${num(q.ask)} × ${q.ask_size ?? "—"}` : undefined],
          ["Open / high / low", isNum(q.open) ? `${num(q.open)} / ${num(q.high)} / ${num(q.low)}` : undefined],
          ["VWAP", isNum(q.vwap) ? num(q.vwap) : undefined],
          ["Volume", isNum(q.volume) ? q.volume.toLocaleString() : undefined],
          ["Reference close", isNum(q.prev_close) ? num(q.prev_close) : undefined],
          ["Events / dup / out-of-order / late", q.stats ? `${q.stats.events} / ${q.stats.duplicates} / ${q.stats.out_of_order} / ${q.stats.late}` : undefined],
          ["Method", q.method],
          ["Status reason", q.reason],
        ]}
      />
    </span>
  );
}
