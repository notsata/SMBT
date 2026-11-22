// Typed client for the backend. Same-origin /api in production; proxied in `next dev`. No auth, no storage.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type ApiInit = RequestInit & { json?: unknown };

export async function api<T>(path: string, init?: ApiInit): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api${path}`, {
    cache: "no-store",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- response types

export type DataStatus = "LIVE" | "DELAYED" | "STALE" | "CLOSED" | "UNAVAILABLE";

export type Card = {
  label: string;
  value: number | string | null;
  status: DataStatus;
  basis: string;
  as_of: string | null;
  provider: string | null;
  method: string;
  note?: string | null;
  available: boolean;
  display?: string;
  reasons?: string[];
  coverage?: number | null;
  kind?: string;
  direction?: string;
  percentile?: number | null;
  term_ratio?: number | null;
  pct_above_200?: number | null;
  mcclellan?: number | null;
  nhnl?: number | null;
  components?: Record<string, number | null>;
  confirmed?: number | string | null;
  confirmed_as_of?: string;
};

export type Quote = {
  symbol: string;
  status: DataStatus;
  reason?: string;
  session?: string;
  provider?: string;
  feed?: string;
  authority?: string;
  kind?: string;
  delay_s?: number;
  last?: number | null;
  last_ts?: string | null;
  recv_ts?: string | null;
  bid?: number | null;
  ask?: number | null;
  bid_size?: number | null;
  ask_size?: number | null;
  quote_ts?: string | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  vwap?: number | null;
  prev_close?: number | null;
  change?: number | null;
  change_pct?: number | null;
  ext_last?: number | null;
  ext_ts?: string | null;
  session_date?: string | null;
  stats?: { events: number; duplicates: number; out_of_order: number; late: number };
  method?: string;
};

export type SessionInfo = { session: string; server_time: string; trading_day: boolean; date: string; open?: string; close?: string; early_close?: boolean; next_open: string };

export type Component = { id: string; label: string; category: string; value: number | null; category_weight: number; detail: string; available: boolean };
export type Explain = { date: string; score: number | null; coverage: number; regime: string; components: Component[]; kind: string };
export type Dist = { n: number; mean?: number; median?: number; hit_rate?: number; pct?: Record<string, number>; episodes?: number; mean_ci95?: number[] | null };

export type ProbRow = {
  event: string;
  horizon: number;
  available: boolean;
  probability?: number | null;
  base_rate?: number | null;
  bucket?: number[];
  episodes?: number;
  days?: number;
  ci95?: number[] | null;
  sufficient?: boolean;
  oos?: { n: number; brier_skill: number | null; note?: string };
};

export type Overview = {
  symbol: string;
  session: SessionInfo;
  quote: Quote | null;
  confirmed_as_of: string;
  provisional_as_of: string | null;
  price: { close: number | null; change_1d: number | null; provider: string; authority: string; adjustment: string };
  cards: Record<"regime" | "bottom" | "top" | "trend" | "vix" | "breadth" | "sentiment" | "macro", Card>;
  explain: { bottom: Explain; top: Explain; trend: Record<string, number | null> };
  probabilities: { kind: string; method: string; as_of: string; bottom: ProbRow[]; top: ProbRow[] };
  meta: { sources: string[]; price: Record<string, unknown>; breadth: { available: boolean; note?: string } };
  limitations: Record<string, string>;
  licensing: { access_mode: string; licensed_for_public_display: string[] | string };
};

export type LiveScores = { symbol: string; as_of: string; session_date: string; rsi14?: number | null; status: DataStatus; bottom: number | null; top: number | null; trend: number | null; regime: string | null; price: number | null; sources: string[] };

export type Bar = { t: string; o: number | null; h: number | null; l: number | null; c: number; v: number | null };
export type MinuteBar = { t: number; open: number; high: number; low: number; close: number; volume: number; vwap?: number | null; final: boolean; source: string };
export type ChartData = {
  symbol: string;
  provider: string;
  authority: string;
  adjustment: string;
  last_confirmed: string;
  provisional: { provider: string; feed: string; status: DataStatus; as_of: string } | null;
  bars: Bar[];
  overlays?: Record<string, (number | null)[]>;
  avwap_anchor?: string;
  volume_profile?: { poc: number; value_area_low: number; value_area_high: number; lookback: number } | null;
};
export type Intraday = { symbol: string; status: DataStatus; reason: string; provider: string; feed: string; session_date: string; bars: MinuteBar[]; vwap?: number | null };

export type SignalEvent = {
  date: string;
  type: string;
  label: string;
  score?: number | null;
  regime?: string;
  triggered_by: { label: string; value?: unknown; category?: string; detail?: string }[];
  forward_realized: Record<string, number | null>;
};

export type Symbols = { benchmarks: string[]; universe: string[]; default: string };

export type Analysis = {
  symbol: string;
  name: string;
  profile: { name: string; market_cap: number | null; sector: string; industry: string; exchange: string; is_etf: boolean; cap_bucket: string; sector_etf: string } | null;
  as_of: string;
  provider: string;
  authority: string;
  adjustment: string;
  quote: Quote | null;
  state: string;
  state_label: string;
  reasons: string[];
  live: { as_of: string; status: DataStatus; provider: string; state: string; state_label: string; indicators: Record<string, number | null> } | null;
  state_forward_stats: Record<string, { n_samples: number; median: number | null; hit_rate: number | null; unconditional_median: number | null; unconditional_hit_rate: number | null }>;
  state_stats_note: string;
  indicators: Record<string, number | null>;
  percentiles: Record<string, number | null>;
  recent_divergences: { type: string; confirmed: string; pivot1: string; pivot2: string; price: number[]; osc: number[] }[];
  sector: { etf: string; sector?: string; sector_rs63_vs_benchmark?: number | null; stock_rs63_vs_sector?: number | null } | null;
  market: { benchmark: string; regime: string; bottom: number | null; top: number | null; basis: string } | null;
  fundamentals: { provider?: string; ratios?: Record<string, unknown>; metrics?: Record<string, unknown>; earnings?: Record<string, unknown>[];
    estimates?: Record<string, unknown>[]; dividends?: Record<string, unknown>[]; splits?: Record<string, unknown>[]; holdings?: Record<string, unknown>[]; unavailable: string[] };
  options: { put_call_volume: number | null; put_call_oi: number | null; atm_iv_30d: number | null; gamma_exposure_usd_per_1pct: number; method: string; as_of: string; contracts: number; kind: string } | { unavailable: string } | null;
  benchmark: string;
};

export type ScanRow = {
  symbol: string;
  name: string;
  sector: string;
  kind: string;
  cap_bucket: string;
  market_cap: number | null;
  provider: string;
  as_of: string;
  close: number | null;
  rsi14: number | null;
  dist200: number | null;
  drawdown: number | null;
  rs63: number | null;
  vol_ratio: number | null;
  flags: string[];
  bullish_confluence: number;
  bullish_evidence: number;
  bearish_confluence: number;
  bearish_evidence: number;
};
export type Scan = {
  rows: ScanRow[];
  total: number;
  sectors: string[];
  flags: Record<string, { label: string; direction: string }>;
  flag_evidence: Record<string, { label: string; direction: string; n: number; excess_mean: number | null; excess_hit_rate: number | null; t_stat: number | null }>;
  skipped: { symbol: string; reason: string }[];
  providers: string[];
  profiles: string | null;
  as_of: string;
  note: string;
  horizon: number;
};

export type Metrics = {
  n_days: number;
  years?: number;
  total_return?: number;
  cagr?: number;
  volatility?: number;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number;
  calmar?: number | null;
  exposure?: number;
  trades?: number;
  win_rate?: number;
  avg_trade?: number;
  profit_factor?: number | null;
};

export type Backtest = {
  strategy: string;
  name: string;
  description: string;
  error?: string;
  symbol: string;
  folds: { train: string[]; test: string[]; chosen_params: Record<string, number>; train_sharpe: number | null; test_metrics: Metrics; test_benchmark: Metrics }[];
  oos_period: string[];
  oos: Metrics;
  oos_benchmark: Metrics;
  other_benchmarks?: Record<string, Metrics>;
  oos_vs_benchmark: { sharpe_diff?: number; sharpe_diff_ci95?: number[]; ann_return_diff?: number; ann_return_diff_ci95?: number[] };
  oos_by_market_state: Record<string, { n_days: number; ann_return?: number; bench_ann_return?: number; sharpe?: number | null; bench_sharpe?: number | null }>;
  equity: { t: string; equity: number; bench_equity: number }[];
  assumptions: Record<string, string | number>;
  in_sample_defaults: { params: Record<string, number>; metrics: Metrics; warning: string };
  purged_cv?: { folds: { test: string[]; chosen_params: Record<string, number>; test_metrics: Metrics; benchmark: Metrics; beat_benchmark_sharpe: boolean }[]; share_folds_beating_benchmark: number; error?: string };
  data: { provider: string; authority: string; adjustment: string; first: string; last: string; sources: string[]; basis: string; survivorship: string };
};

export type Strategy = { id: string; name: string; description: string; grid: Record<string, number[]>; defaults: Record<string, number>; needs: string[] };

export type ScoreRow = {
  signal: string;
  label: string;
  direction: string;
  n_events: number;
  median: number | null;
  hit_rate: number | null;
  baseline_hit_rate: number | null;
  excess_mean: number | null;
  p_value: number | null;
  q_value: number | null;
  significant: boolean;
  significant_after_fdr: boolean;
  active_now: boolean;
};

export type HorizonStat = Dist & { baseline?: Dist; p_value_vs_random?: number; excess_mean?: number; excess_median?: number; mae_median?: number; significant_5pct?: boolean };
export type SignalReport = {
  signal: string;
  label: string;
  direction: string;
  event_study: { n_events: number; events: string[]; horizons: Record<string, HorizonStat> };
  turning_points: { pivots: number; signals: number; precision: number | null; recall: number | null; false_positives?: number; missed_pivots?: number; median_lag_sessions?: number | null; window: number; pivot_threshold: number; note?: string };
  stability_by_era: { period: string[]; n_events: number; excess_mean: number | null; hit_rate: number | null; baseline_hit_rate: number | null }[];
  method: string;
};

export type Analogs = {
  symbol: string;
  as_of?: string;
  sources?: string[];
  features_used?: string[];
  matches: { date: string; similarity: number; event: string | null; forward: Record<string, number | null> }[];
  outcome_distribution?: Record<string, Dist>;
  unconditional?: Record<string, Dist>;
  key_dates: { date: string; name: string; kind: string; similarity: number; features_compared: number; forward: Record<string, number | null> }[];
  note?: string;
};

export type StreamStatus = { provider: string; feed: string; kind: string; delay_s: number; state: string; error: string | null; active: boolean; symbols: number; max_symbols: number; messages: number; reconnects: number; last_message: string | null };
export type Status = {
  session: SessionInfo;
  licensing: { access_mode: string; licensed_for_public_display: string[] | string };
  streams: StreamStatus[];
  live: Quote[];
  providers: Record<string, { configured: boolean; roles: string[]; [k: string]: unknown }>;
  history: { symbol: string; loaded: boolean; provider?: string; authority?: string; adjustment?: string; first?: string; last?: string; fetched_at?: string; complete_through_last_session?: boolean; issues?: string[]; provider_errors?: string[] }[];
  macro: { id: string; name: string; category: string; freq: string; note: string; available: boolean; current: boolean; last_obs: string | null; known_since: string | null; source: string | null; fred_last_updated: string | null; copyright: boolean | null; error: string | null; loaded: boolean }[];
  breadth: { available: boolean; note?: string; members_today?: number; members_with_data_today?: number; first?: string; last?: string; providers?: string[]; provisional_as_of?: string | null };
  limitations: Record<string, string>;
  now: string;
};

export type AlertRule = { id: string; name: string; kind: string; params: Record<string, unknown>; cooldown_minutes: number };
export type AlertEvent = { rule_id: string; fired_at: string; title: string; body: string };
export type AlertKinds = { kinds: Record<string, string>; scores: Record<string, string>; breadth_metrics: Record<string, string>; scanner_flags: Record<string, string>; note: string };
export type AnalystAnswer = { answer: string; citations: { tool: string; symbol?: string; as_of?: string | null; source?: string }[]; tool_calls: { tool: string; input: Record<string, unknown> }[]; model: string };
