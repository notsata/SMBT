// Typed client for the FastAPI backend. Same-origin /api in production; proxied in `next dev`.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Listener = (cachedAt: string | null) => void;
const offlineListeners = new Set<Listener>();
export function onOfflineData(fn: Listener) {
  offlineListeners.add(fn);
  return () => {
    offlineListeners.delete(fn);
  };
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  // The service worker marks responses it served from cache while offline.
  const cached = res.headers.get("x-smbt-cached-at");
  offlineListeners.forEach((fn) => fn(cached));
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = `/login/?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
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

export type Card = {
  label: string;
  value: number | string | null;
  as_of: string | null;
  source: string;
  stale: boolean | null;
  available: boolean;
  display?: string;
  reasons?: string[];
  coverage?: number | null;
  kind?: string;
  direction?: string;
  percentile?: number | null;
  term_ratio?: number | null;
  note?: string;
  pct_above_200?: number | null;
  mcclellan?: number | null;
  nhnl?: number | null;
  components?: Record<string, number | null>;
};

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
  oos?: { n: number; brier_skill: number | null; brier_model?: number; brier_base_rate?: number; note?: string; calibration?: { pred: number[]; n: number; mean_pred: number; realized: number }[] };
  return_stats?: Dist & { baseline?: Dist } | null;
};

export type Overview = {
  symbol: string;
  as_of: string;
  data_kind: string;
  price: { close: number | null; change_1d: number | null; source: string; quote?: { price: number; time: string; source: string; delay: string } | null };
  cards: Record<"regime" | "bottom" | "top" | "trend" | "vix" | "breadth" | "sentiment" | "macro", Card>;
  explain: { bottom: Explain; top: Explain; trend: Record<string, number | null> };
  probabilities: { kind: string; method: string; as_of: string; bottom: ProbRow[]; top: ProbRow[] };
  meta: { price: { source: string; last_date: string }; breadth: { available: boolean; note?: string; survivorship_bias?: boolean } };
  unavailable: Record<string, string>;
};

export type Bar = { t: string; o: number | null; h: number | null; l: number | null; c: number; v: number | null };
export type ChartData = {
  symbol: string;
  source: string;
  last_date: string;
  bars: Bar[];
  overlays?: Record<string, (number | null)[]>;
  avwap_anchor?: string;
  volume_profile?: { poc: number; value_area_low: number; value_area_high: number; lookback: number } | null;
};

export type SignalEvent = {
  date: string;
  type: string;
  label: string;
  score?: number | null;
  regime?: string;
  triggered_by: { label: string; value?: unknown; category?: string; detail?: string }[];
  forward_realized: Record<string, number | null>;
};

export type SymbolInfo = { symbol: string; name: string; sector: string; kind: string; cap_bucket: string; first: string; last: string; rows: number; source: string };

export type Analysis = {
  symbol: string;
  name: string;
  as_of: string;
  source: string;
  state: string;
  state_label: string;
  reasons: string[];
  state_forward_stats: Record<string, { n_samples: number; median: number | null; hit_rate: number | null; unconditional_median: number | null; unconditional_hit_rate: number | null }>;
  state_stats_note: string;
  indicators: Record<string, number | null>;
  percentiles: Record<string, number | null>;
  recent_divergences: { type: string; confirmed: string; pivot1: string; pivot2: string; price: number[]; osc: number[] }[];
  sector: { etf: string; sector?: string; sector_rs63_vs_benchmark?: number | null; stock_rs63_vs_sector?: number | null; note?: string } | null;
  market: { benchmark: string; regime: string; bottom: number | null; top: number | null } | null;
  valuation: { available: boolean; note: string };
  benchmark: string;
};

export type ScanRow = {
  symbol: string;
  name: string;
  sector: string;
  kind: string;
  cap_bucket: string;
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
  oos_vs_benchmark: { sharpe_diff?: number; sharpe_diff_ci95?: number[]; p_sharpe_diff_le_0?: number; ann_return_diff?: number; ann_return_diff_ci95?: number[] };
  oos_by_market_state: Record<string, { n_days: number; ann_return?: number; bench_ann_return?: number; sharpe?: number | null; bench_sharpe?: number | null }>;
  equity: { t: string; equity: number; bench_equity: number }[];
  assumptions: Record<string, string | number>;
  in_sample_defaults: { params: Record<string, number>; metrics: Metrics; warning: string };
  purged_cv?: { folds: { test: string[]; chosen_params: Record<string, number>; test_metrics: Metrics; benchmark: Metrics; beat_benchmark_sharpe: boolean }[]; share_folds_beating_benchmark: number; error?: string };
  data: { source: string; first: string; last: string; survivorship: string };
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
  features_used?: string[];
  matches: { date: string; similarity: number; event: string | null; forward: Record<string, number | null> }[];
  outcome_distribution?: Record<string, Dist>;
  unconditional?: Record<string, Dist>;
  key_dates: { date: string; name: string; kind: string; similarity: number; features_compared: number; forward: Record<string, number | null> }[];
  note?: string;
};

export type Status = {
  symbols_loaded: number;
  prices: { symbol: string; first: string; last: string; rows: number; source: string; fetched_at: string; stale: boolean }[];
  series: { id: string; name: string; category: string; freq: string; default_source: string; note: string; available: boolean; last_obs?: string; available_at?: string; source?: string }[];
  breadth: { available: boolean; note?: string; members?: number; with_data?: number };
  recent_errors: { source: string; target: string; at: string; message: string }[];
  auth_enabled: boolean;
  providers: Record<string, boolean>;
  unavailable: Record<string, string>;
  data_version: number;
};

export type AlertRule = { id: number; name: string; kind: string; params: Record<string, unknown>; enabled: boolean; cooldown_minutes: number; armed: boolean; last_fired_at: string | null };
export type AlertKinds = { kinds: Record<string, string>; scores: Record<string, string>; breadth_metrics: Record<string, string>; scanner_flags: Record<string, string> };
export type AlertEvent = { id: number; rule_id: number; fired_at: string; title: string; body: string; delivered: number };

export type AnalystAnswer = { answer: string; citations: { tool: string; symbol?: string; as_of?: string | null; source?: string }[]; tool_calls: { tool: string; input: Record<string, unknown> }[]; model: string };
