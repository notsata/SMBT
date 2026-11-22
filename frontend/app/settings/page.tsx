"use client";

import { useEffect, useState } from "react";
import { applyTheme } from "@/components/Shell";
import { Badge, ErrorBox, Inspector, Note, Panel, Segmented, Spinner, StatusBadge, Td, Th } from "@/components/ui";
import type { Status } from "@/lib/api";
import { num } from "@/lib/format";
import { useConnection } from "@/lib/live";
import { useApi } from "@/lib/useApi";

type Theme = "dark" | "light" | "system";

/** Theme for this tab only: read from the page, never from storage. */
function ThemePicker() {
  const [t, setT] = useState<Theme>("system");
  useEffect(() => setT((document.documentElement.getAttribute("data-theme") as Theme | null) ?? "system"), []);
  return (
    <Segmented label="Theme" value={t} onChange={(v) => { setT(v); applyTheme(v); }} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "System" }]} />
  );
}

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

export default function SettingsPage() {
  const st = useApi<Status>("/status");
  const [conn] = useConnection().split("|");
  const s = st.data;
  useEffect(() => {
    const id = setInterval(st.reload, 15_000);
    return () => clearInterval(id);
  }, [st.reload]);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Data sources</h1>
      <Panel title="This device">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <ThemePicker />
          <Badge tone={conn === "open" ? "up" : "warn"}>live connection: {conn}</Badge>
        </div>
        <Note>
          No account, login or saved data: the theme, watchlist and alert rules last only while this tab is open, and nothing is written to browser storage. Install: iPhone Safari →
          Share → Add to Home Screen; Windows Chrome/Edge → install icon in the address bar.
        </Note>
      </Panel>
      {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
      {!s && !st.error && <Spinner />}
      {s && (
        <>
          <Panel title="Market session & licensing" right={<span className="num">server {when(s.now)}</span>}>
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                Session: <b>{s.session.session}</b> ({s.session.date}{s.session.trading_day ? "" : ", not a trading day"}){s.session.early_close && <Badge tone="warn">early close</Badge>}
                <div className="text-xs text-muted">
                  {s.session.open && `Regular hours ${when(s.session.open)} – ${when(s.session.close)} · `}next open {when(s.session.next_open)} · XNYS calendar
                </div>
              </div>
              <div>
                Access mode: <b>{s.licensing.access_mode}</b>
                <div className="text-xs text-muted">
                  Shown to visitors: {Array.isArray(s.licensing.licensed_for_public_display) ? s.licensing.licensed_for_public_display.join(", ") : s.licensing.licensed_for_public_display}
                </div>
              </div>
            </div>
            <Note>
              In public mode, data from a provider is served only if the operator declares a licence that permits public display for it (PUBLIC_DISPLAY_LICENSES); anything else returns
              &ldquo;not licensed&rdquo; instead of a value.
            </Note>
          </Panel>

          <Panel title="Streaming feeds">
            {s.streams.length === 0 && <p className="text-sm text-muted">No streaming provider configured: prices are daily closes only, labelled DELAYED/CLOSED.</p>}
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Provider</Th><Th>Feed</Th><Th>Kind</Th><Th>Delay</Th><Th>State</Th><Th>Symbols</Th><Th>Messages</Th><Th>Reconnects</Th><Th>Last message</Th><Th>Error</Th></tr></thead>
                <tbody>
                  {s.streams.map((x) => (
                    <tr key={x.provider + x.feed}>
                      <Td>{x.provider}{x.active && <Badge tone="accent">active</Badge>}</Td><Td>{x.feed}</Td><Td>{x.kind}</Td><Td>{x.delay_s ? `${Math.round(x.delay_s / 60)} min` : "real time"}</Td>
                      <Td><Badge tone={x.state === "connected" ? "up" : x.state === "connecting" ? "warn" : "down"}>{x.state}</Badge></Td>
                      <Td>{x.symbols}/{x.max_symbols || "∞"}</Td><Td>{x.messages.toLocaleString()}</Td><Td>{x.reconnects}</Td><Td className="text-muted">{when(x.last_message)}</Td>
                      <Td className="max-w-[16rem] whitespace-normal text-down">{x.error}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Live symbols on this server">
            {s.live.length === 0 && <p className="text-sm text-muted">No symbols streaming.</p>}
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Symbol</Th><Th>Status</Th><Th>Last</Th><Th>Event time</Th><Th>Received</Th><Th>Provider</Th><Th>Events / dup / out-of-order / late</Th></tr></thead>
                <tbody>
                  {s.live.map((q) => (
                    <tr key={q.symbol}>
                      <Td>{q.symbol}</Td><Td><StatusBadge status={q.status} reason={q.reason} /></Td><Td>{num(q.last)}</Td><Td className="text-muted">{when(q.last_ts)}</Td>
                      <Td className="text-muted">{when(q.recv_ts)}</Td><Td>{q.provider} {q.feed}</Td>
                      <Td>{q.stats ? `${q.stats.events} / ${q.stats.duplicates} / ${q.stats.out_of_order} / ${q.stats.late}` : "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Providers">
            <ul className="grid gap-2 text-xs sm:grid-cols-2">
              {Object.entries(s.providers).map(([k, v]) => (
                <li key={k} className="rounded-lg bg-panel2 p-2">
                  <div className="flex items-center gap-2">
                    <b className="text-sm">{k}</b>
                    <Badge tone={v.configured ? "up" : "muted"}>{v.configured ? "configured" : "no key"}</Badge>
                  </div>
                  <div className="text-muted">{v.roles.join(" · ")}</div>
                  <Inspector rows={Object.entries(v).filter(([kk]) => !["configured", "roles"].includes(kk)).map(([kk, vv]) => [kk, String(vv)])} />
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Daily history (in memory, fetched on demand)">
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Symbol</Th><Th>Provider</Th><Th>Adjustment</Th><Th>From</Th><Th>Last close</Th><Th>Complete</Th><Th>Fetched</Th><Th>Issues</Th></tr></thead>
                <tbody>
                  {s.history.map((h) => (
                    <tr key={h.symbol} className={h.loaded ? "" : "text-muted"}>
                      <Td>{h.symbol}</Td><Td>{h.loaded ? h.provider : "not loaded yet"}</Td><Td>{h.adjustment}</Td><Td>{h.first}</Td><Td>{h.last}</Td>
                      <Td>{h.loaded && (h.complete_through_last_session ? <Badge tone="up">yes</Badge> : <Badge tone="warn">behind</Badge>)}</Td>
                      <Td className="text-muted">{when(h.fetched_at)}</Td>
                      <Td className="max-w-[18rem] whitespace-normal text-muted">{[...(h.issues ?? []), ...(h.provider_errors ?? [])].join("; ")}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Macro series (FRED, point-in-time)">
            <div className="scroll-x">
              <table className="w-full text-xs">
                <thead><tr><Th>Series</Th><Th>Name</Th><Th>Freq</Th><Th>Last obs</Th><Th>Known from</Th><Th>FRED updated</Th><Th>Status</Th><Th>Notes</Th></tr></thead>
                <tbody>
                  {s.macro.map((m) => (
                    <tr key={m.id} className={m.available ? "" : "text-muted"}>
                      <Td>{m.id}</Td><Td className="whitespace-normal">{m.name}</Td><Td>{m.freq}</Td><Td>{m.last_obs ?? "—"}</Td><Td>{m.known_since ?? ""}</Td>
                      <Td className="text-muted">{m.fred_last_updated ?? ""}</Td>
                      <Td>
                        {!m.available ? <Badge tone="down">unavailable</Badge> : m.current ? <Badge tone="accent">current</Badge> : <Badge tone="warn">stale</Badge>}
                        {m.copyright && <Badge tone="warn" title="Copyrighted third-party series: excluded in public mode unless licensed">©</Badge>}
                      </Td>
                      <Td className="max-w-[18rem] whitespace-normal text-muted">{m.error ?? m.note}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="S&P 500 breadth">
            <p className="text-sm">
              {s.breadth.available ? (
                <>
                  {s.breadth.members_with_data_today}/{s.breadth.members_today} current members with data · {s.breadth.first} → {s.breadth.last} · {s.breadth.providers?.join(", ")}
                  {s.breadth.provisional_as_of && ` · intraday snapshot ${when(s.breadth.provisional_as_of)}`}
                </>
              ) : (
                <Badge tone="warn">unavailable</Badge>
              )}
            </p>
            {s.breadth.note && <Note>{s.breadth.note}</Note>}
          </Panel>

          <Panel title="Not shown (no licensed or trustworthy source)">
            <ul className="space-y-1 text-xs text-muted">
              {Object.entries(s.limitations).map(([k, v]) => (
                <li key={k}><b className="text-fg/80">{k.replaceAll("_", " ")}</b>: {v}</li>
              ))}
            </ul>
          </Panel>
        </>
      )}
    </div>
  );
}
