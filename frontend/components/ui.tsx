"use client";

import { type ReactNode, useState } from "react";
import { isNum, pct } from "@/lib/format";

type Tone = "up" | "down" | "warn" | "muted" | "accent";
const toneText: Record<Tone, string> = { up: "text-up", down: "text-down", warn: "text-warn", muted: "text-muted", accent: "text-accent" };
const toneBg: Record<Tone, string> = {
  up: "bg-up/12 text-up border-up/30",
  down: "bg-down/12 text-down border-down/30",
  warn: "bg-warn/12 text-warn border-warn/30",
  muted: "bg-panel2 text-muted border-line",
  accent: "bg-accent/12 text-accent border-accent/30",
};

export function Panel({ title, right, children, className = "", id }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={`rounded-xl border border-line bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold tracking-wide">{title}</h2>
          <div className="flex items-center gap-2 text-xs text-muted">{right}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Badge({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 ${toneBg[tone]}`}>
      {children}
    </span>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}…
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-down/40 bg-down/10 p-3 text-sm text-down">
      {error.message}
      {onRetry && (
        <button onClick={onRetry} className="ml-3 underline">
          Retry
        </button>
      )}
    </div>
  );
}

const STATUS_STYLE: Record<string, { tone: Tone; tip: string }> = {
  LIVE: { tone: "up", tip: "Confirmed real-time feed; updates as events arrive." },
  DELAYED: { tone: "accent", tip: "Known provider or publication delay (e.g. 15-minute feed, prior close, official release schedule)." },
  STALE: { tone: "warn", tip: "Previously verified but no longer current." },
  CLOSED: { tone: "muted", tip: "Market session inactive; last verified values." },
  UNAVAILABLE: { tone: "down", tip: "No trustworthy value can be shown." },
};

export function StatusBadge({ status, reason }: { status?: string | null; reason?: string | null }) {
  const s = STATUS_STYLE[status ?? "UNAVAILABLE"] ?? STATUS_STYLE.UNAVAILABLE;
  return (
    <Badge tone={s.tone} title={`${s.tip}${reason ? " · " + reason : ""}`}>
      {status === "LIVE" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-up" aria-hidden />}
      {status ?? "UNAVAILABLE"}
    </Badge>
  );
}

function when(iso?: string | null) {
  if (!iso) return "—";
  if (iso.length === 10) return iso;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Expandable provenance: source, authority, timestamps, session, delay, method. */
export function Inspector({ rows }: { rows: [string, unknown][] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1 text-[11px]">
      <button onClick={(e) => { e.stopPropagation(); setOpen(!open); }} aria-expanded={open} className="text-muted underline decoration-dotted hover:text-fg">
        {open ? "hide source" : "source & method"}
      </button>
      {open && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 rounded-md bg-panel2 p-2 text-left">
          {rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">{typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) ? when(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function StatusLine({ status, asOf, reason }: { status?: string | null; asOf?: string | null; reason?: string | null }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
      <StatusBadge status={status} reason={reason} />
      {asOf && <span className="num">{when(asOf)}</span>}
    </div>
  );
}

export function Gauge({ value, invert = false, label }: { value: number | null | undefined; invert?: boolean; label?: string }) {
  const v = isNum(value) ? Math.max(0, Math.min(100, value)) : null;
  const tone = v === null ? "bg-line" : (v >= 70) !== invert ? (invert ? "bg-down" : "bg-up") : v >= 40 ? "bg-warn" : invert ? "bg-up" : "bg-muted";
  return (
    <div aria-label={label} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v ?? undefined} className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-panel2">
      {v !== null && <div className={`h-full rounded-full ${tone}`} style={{ width: `${v}%` }} />}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-panel2 p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1.5 font-medium ${value === o.value ? "bg-panel text-fg shadow-sm" : "text-muted hover:text-fg"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Horizontal percentile strip: 5-95 whiskers, 25-75 box, median tick, optional baseline median. */
export function DistBar({ p, baselineMedian, domain = 0.3 }: { p?: Record<string, number>; baselineMedian?: number; domain?: number }) {
  if (!p) return <span className="text-muted">—</span>;
  const x = (v: number) => `${Math.max(0, Math.min(100, ((v + domain) / (2 * domain)) * 100))}%`;
  return (
    <div className="relative h-5 w-full min-w-[120px]" title={`5%: ${pct(p["5"])}  25%: ${pct(p["25"])}  median: ${pct(p["50"])}  75%: ${pct(p["75"])}  95%: ${pct(p["95"])}`}>
      <div className="absolute top-1/2 h-px w-full bg-line" />
      <div className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-muted/60" style={{ left: x(0) }} />
      <div className="absolute top-1/2 h-px -translate-y-1/2 bg-muted" style={{ left: x(p["5"]), width: `calc(${x(p["95"])} - ${x(p["5"])})` }} />
      <div className="absolute top-1/2 h-3 -translate-y-1/2 rounded-sm bg-accent/35" style={{ left: x(p["25"]), width: `calc(${x(p["75"])} - ${x(p["25"])})` }} />
      <div className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 bg-accent" style={{ left: x(p["50"]) }} />
      {isNum(baselineMedian) && <div className="absolute top-0 h-5 w-0.5 bg-warn" style={{ left: x(baselineMedian) }} title={`baseline median ${pct(baselineMedian)}`} />}
    </div>
  );
}

export function Expand({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-center justify-between text-left text-xs text-accent">
        {summary}
        <span aria-hidden>{open ? "▴" : "▾"}</span>
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function Th({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return <th className={`whitespace-nowrap border-b border-line px-2 py-1.5 text-left text-[11px] font-medium uppercase tracking-wide text-muted ${className}`}>{children}</th>;
}

export function Td({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return <td className={`num whitespace-nowrap border-b border-line/60 px-2 py-1.5 ${className}`}>{children}</td>;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-xs leading-relaxed text-muted">{children}</p>;
}

export function KindTag({ kind }: { kind: "heuristic" | "empirical" | "similarity" }) {
  const map = {
    heuristic: { tone: "accent" as Tone, text: "Heuristic score", tip: "Weighted composite of indicator percentiles. Not a probability." },
    empirical: { tone: "up" as Tone, text: "Empirical frequency", tip: "Counted from history with sample sizes; validated out of sample." },
    similarity: { tone: "warn" as Tone, text: "Historical similarity", tip: "Nearest past conditions. Small samples; context, not odds." },
  }[kind];
  return (
    <Badge tone={map.tone} title={map.tip}>
      {map.text}
    </Badge>
  );
}

export { toneText };
