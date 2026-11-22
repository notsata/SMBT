// Serves the app's API calls from recorded responses of the real backend (packaging/artifact/export_snapshot.py),
// and answers the AI analyst through the artifact's `sample` capability with the same tools and data.
import { ApiError, type ApiInit, type AnalystAnswer } from "@/lib/api";

type Rec = Map<string, unknown>;
type Json = Record<string, unknown>;
const SYMBOLS = ["^GSPC", "^IXIC"];
const DEFAULT = "^GSPC";
const PC_APP = "the SMBT PC app or your own server";

const files: Record<string, Promise<Rec>> = {};
function load(name: string): Promise<Rec> {
  files[name] ??= fetch(`data/${name}.json`)
    .then((r) => {
      if (!r.ok) throw new ApiError(r.status, `Could not load the ${name} data file (HTTP ${r.status}).`);
      return r.json() as Promise<{ key: string; data: unknown }[]>;
    })
    .then((rows) => new Map(rows.map((x) => [x.key, x.data])))
    .catch((e) => {
      delete files[name]; // let a later view retry
      throw e instanceof ApiError ? e : new ApiError(503, `Could not load the ${name} data file. Check your connection and reload.`);
    });
  return files[name];
}

function notIncluded(what: string): ApiError {
  const sym = what.match(/\^?[A-Z][A-Z0-9.\-^]*/g)?.find((s) => s.length > 1 && !SYMBOLS.includes(s));
  return new ApiError(
    404,
    sym
      ? `${sym} isn't in this snapshot. It covers ^GSPC (S&P 500) and ^IXIC (NASDAQ Composite) from the bundled 1999–2018 data. Other tickers need live data from ${PC_APP}.`
      : `Not included in this snapshot (${what}).`,
  );
}

function pick(map: Rec, key: string): unknown {
  if (!map.has(key)) throw notIncluded(key);
  return map.get(key);
}

function scan(core: Rec, q: URLSearchParams) {
  const data = pick(core, `scan:${q.get("timeframe") || "daily"}`) as Json & { rows: Json[] };
  let rows = data.rows;
  const eq = (k: string, v: string | null) => (v ? rows.filter((r) => String(r[k]).toLowerCase() === v.toLowerCase()) : rows);
  rows = eq("sector", q.get("sector"));
  rows = eq("kind", q.get("kind"));
  rows = eq("cap_bucket", q.get("cap"));
  const signal = q.get("signal");
  if (signal) rows = rows.filter((r) => (r.flags as string[]).includes(signal));
  const term = q.get("q");
  if (term) rows = rows.filter((r) => String(r.symbol).includes(term.toUpperCase()) || String(r.name).toLowerCase().includes(term.toLowerCase()));
  const d = q.get("direction");
  if (d === "bullish" || d === "bearish") {
    rows = [...rows].sort((a, b) => (b[`${d}_confluence`] as number) - (a[`${d}_confluence`] as number) || (b[`${d}_evidence`] as number) - (a[`${d}_evidence`] as number) || String(a.symbol).localeCompare(String(b.symbol)));
  }
  return { ...data, rows: rows.slice(0, 100), total: rows.length };
}

async function indicators(sym: string, names: string) {
  const all = (await load("indicators")).get(sym) as { t: string[]; series: Record<string, unknown>; labels: Record<string, string>; available: string[] } | undefined;
  if (!all) throw notIncluded(sym);
  const series: Record<string, unknown> = {};
  const labels: Record<string, string> = {};
  for (const n of names.split(",").filter(Boolean)) {
    if (all.series[n]) {
      series[n] = all.series[n];
      labels[n] = all.labels[n];
    }
  }
  return { symbol: sym, t: all.t, series, labels, available: all.available };
}

async function backtest(body: Json) {
  const custom = Number(body.train_years ?? 6) !== 6 || Number(body.test_years ?? 2) !== 2 || Number(body.commission_bps ?? 1) !== 1 || Number(body.slippage_bps ?? 2) !== 2;
  if (custom) {
    throw new ApiError(400, `This snapshot holds walk-forward results for the default settings (6-year train, 2-year test, 1 + 2 bps costs). Reset those inputs, or run custom settings in ${PC_APP}.`);
  }
  return pick(await load("backtests"), `${String(body.symbol ?? DEFAULT).toUpperCase()}|${body.strategy}`);
}

// ---------------------------------------------------------------- AI analyst via claude.use("sample")

type SampleTool = { name: string; description: string; inputSchema?: unknown; execute: (input: Json) => unknown };
type Sample = (input: { role: "user" | "assistant"; content: string }[], opts: { tools: SampleTool[] }) => Promise<{ text: string; truncated: boolean; modelTierApplied: string }>;
let samplePromise: Promise<Sample | null> | null = null;
function getSample(): Promise<Sample | null> {
  const c = (window as unknown as { claude?: { use?: (n: string) => Promise<unknown> } }).claude;
  samplePromise ??= c?.use ? (c.use("sample") as Promise<Sample | null>).catch(() => null) : Promise.resolve(null);
  return samplePromise;
}

// Same key format as Python's json.dumps(args, sort_keys=True) in the exporter.
function pyJson(args: Json): string {
  return "{" + Object.keys(args).sort().map((k) => `${JSON.stringify(k)}: ${JSON.stringify(args[k])}`).join(", ") + "}";
}

function toolArgs(name: string, input: Json): Json {
  const sym = String(input.symbol || DEFAULT).toUpperCase();
  switch (name) {
    case "get_data_status":
      return {};
    case "run_scanner":
      if (input.kind || input.sector || input.signal) throw new Error("Scanner filters aren't available in this snapshot; call run_scanner with only a direction.");
      return { direction: String(input.direction) };
    case "get_signal_statistics":
      if (input.threshold !== undefined && Number(input.threshold) !== 70) throw new Error("Only the default threshold of 70 is available in this snapshot.");
      return { signal: String(input.signal), symbol: sym };
    case "get_correction_frequency": {
      const t = Number(input.threshold_pct);
      const h = Number(input.horizon_days);
      if (![5, 10, 15, 20].includes(t) || ![20, 60, 126, 252].includes(h)) throw new Error("Snapshot has threshold_pct 5, 10, 15 or 20 and horizon_days 20, 60, 126 or 252.");
      return { horizon_days: h, symbol: sym, threshold_pct: t };
    }
    default:
      return { symbol: sym };
  }
}

const SAMPLE_ERRORS: Record<string, [number, string]> = {
  not_granted: [503, "Claude access was declined for this page. Reload to be asked again."],
  sampling_disabled: [503, "Claude isn't available for this account here."],
  tools_unavailable: [503, "This view can't run the analyst's data tools. Open the page in claude.ai in a current browser or app."],
  rate_limited: [429, "Claude usage limit reached for now. Try again later."],
  refused: [400, "Claude declined this question. Try rephrasing it."],
  session_expired: [401, "Your Claude session expired. Sign in again and reload."],
  cancelled: [499, "Stopped."],
  prompt_too_large: [413, "The conversation got too long. Reload the page to start a new one."],
};

async function askAnalyst(body: Json): Promise<AnalystAnswer> {
  const sample = await getSample();
  if (!sample) throw new ApiError(503, `The AI analyst needs Claude access, which this view doesn't provide. Open the artifact in claude.ai, or use ${PC_APP} with an Anthropic API key.`);
  const an = await load("analyst");
  const meta = an.get("__meta__") as { system: string; tools: { name: string; description: string; input_schema: unknown }[] };
  const citations: AnalystAnswer["citations"] = [];
  const calls: AnalystAnswer["tool_calls"] = [];
  const tools: SampleTool[] = meta.tools.map((t) => ({
    name: t.name,
    description: t.description,
    inputSchema: t.input_schema,
    execute: (input: Json) => {
      const args = toolArgs(t.name, input ?? {});
      const hit = an.get(`${t.name}|${pyJson(args)}`) as { result: unknown; cites: AnalystAnswer["citations"] } | undefined;
      if (!hit) throw new Error(`No data for ${t.name} with ${JSON.stringify(args)}. This snapshot covers ^GSPC and ^IXIC only.`);
      citations.push(...hit.cites);
      calls.push({ tool: t.name, input: args });
      return hit.result;
    },
  }));
  const history = (Array.isArray(body.history) ? body.history : [])
    .filter((m: Json) => (m.role === "user" || m.role === "assistant") && String(m.content ?? "").trim())
    .slice(-10)
    .map((m: Json) => ({ role: m.role as "user" | "assistant", content: String(m.content).slice(0, 8000) }));
  const context =
    "\n\nContext: you are running inside an offline snapshot of this app. Its data was computed by the app's engine from bundled " +
    "1999-2018 history of the S&P 500 (^GSPC, the default) and NASDAQ Composite (^IXIC); no other tickers and no data after 2018 are available. " +
    "Say so when the user asks about anything else, and mention that live data runs in the SMBT PC app.";
  try {
    const r = await sample([{ role: "user", content: meta.system + context }, ...history, { role: "user", content: String(body.question).slice(0, 4000) }], { tools });
    return { answer: r.text + (r.truncated ? "\n\n[Answer cut short at the length limit.]" : ""), citations, tool_calls: calls, model: `Claude (${r.modelTierApplied} tier)` };
  } catch (e) {
    const code = (e as { code?: string }).code ?? "upstream_error";
    const [status, msg] = SAMPLE_ERRORS[code] ?? [502, `Claude couldn't answer right now (${code}). Try again.`];
    throw new ApiError(status, msg);
  }
}

// ---------------------------------------------------------------- router

export async function snapshotTransport(path: string, init?: ApiInit): Promise<unknown> {
  const method = (init?.method ?? "GET").toUpperCase();
  const [rawPath, rawQuery = ""] = path.split("?");
  const p = decodeURIComponent(rawPath);
  const q = new URLSearchParams(rawQuery);
  const body = (init?.json ?? {}) as Json;
  if (method === "POST" && p === "/backtest") return backtest(body);
  if (method === "POST" && p === "/analyst") return askAnalyst(body);
  if (method !== "GET") {
    throw new ApiError(503, `This mobile snapshot is read-only: alerts, push notifications, watchlist changes and data refreshes run in ${PC_APP}.`);
  }
  const core = await load("core");
  const sym = (s: string | null) => (s || DEFAULT).toUpperCase();
  let m: RegExpMatchArray | null;
  if (p === "/overview") return pick(core, `/overview?symbol=${sym(q.get("symbol"))}`);
  if (p === "/analogs") return pick(core, `/analogs?symbol=${sym(q.get("symbol"))}&k=12`);
  if (p === "/scanner") return scan(core, q);
  if ((m = p.match(/^\/chart\/(.+)$/))) return pick(await load("charts"), `/chart/${m[1].toUpperCase()}`);
  if ((m = p.match(/^\/indicators\/(.+)$/))) return indicators(m[1].toUpperCase(), q.get("names") ?? "");
  if ((m = p.match(/^\/signal-stats\/(.+)$/))) return pick(await load("signal-stats"), `${m[1].toUpperCase()}|${q.get("signal")}|${Number(q.get("threshold") ?? 70)}`);
  if ((m = p.match(/^\/(analyze|scoreboard|signals)\/(.+)$/))) return pick(core, `/${m[1]}/${m[2].toUpperCase()}`);
  return pick(core, p);
}
