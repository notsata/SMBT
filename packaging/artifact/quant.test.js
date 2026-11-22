// Checks quant.js against the server's Python (reference JSON written by backend/tests/test_pocket.py).
// Deterministic outputs must match; resampled ones (bootstrap CIs, random-entry p-values) only roughly.
// Usage: node quant.test.js <quant.js> <ref.json>
const assert = require("node:assert/strict");
require(process.argv[2]);
const Q = globalThis.SMBTQ;
const ref = require(process.argv[3]);

const close = (a, b, tol, what) => {
  if (a === null || b === null || a === undefined || b === undefined || Number.isNaN(a) || Number.isNaN(b)) {
    const na = (x) => x === null || x === undefined || Number.isNaN(x);
    return assert.ok(na(a) && na(b), `${what}: ${a} vs ${b}`);
  }
  assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${what}: ${a} vs ${b}`);
};
const f = Q.technicalFrame(ref.bars);
const sf = Q.scoreFrame(f);
let checks = 0;

// technical columns at checkpoints, flag columns in full
for (const [k, rows] of Object.entries(ref.tech)) for (const [i, v] of rows) { close(f[k][i], v, 1e-6, `${k}@${i}`); checks++; }
for (const [k, idx] of Object.entries(ref.flags)) { assert.deepEqual(f[k].flatMap((x, i) => (x ? [i] : [])), idx, `flag ${k}`); checks++; }

// regime, scores, trend
assert.deepEqual(sf.regime, ref.regime, "regime series"); checks++;
for (const k of ["bottom", "top", "trend", "bottom_cov", "top_cov"]) for (const [i, v] of ref.scores[k]) { close(sf[k][i], v, 1e-6, `${k}@${i}`); checks++; }
const ex = Q.explain(sf, f.n - 1, "bottom");
assert.deepEqual(ex.components.map((c) => [c.id, c.value]), ref.explain_bottom.map((c) => [c.id, c.value]), "explain bottom"); checks++;

// probabilities (deterministic parts)
const pr = Q.scoreProbabilities(sf, f.close);
for (const side of ["bottom", "top"]) pr[side].forEach((r, k) => {
  const e = ref.probabilities[side][k];
  for (const key of ["probability", "base_rate", "episodes", "days", "sufficient"]) close(+r[key], +e[key], 1e-9, `${side}.${r.horizon}.${key}`);
  assert.deepEqual(r.bucket, e.bucket); close(r.oos.brier_skill, e.oos.brier_skill, 1e-9, `${side}.${r.horizon}.brier`);
  // bootstrap CI: the server's own bounds move by >0.1 between seeds on small episode counts, so only shape is compared
  assert.equal(!!r.ci95, !!e.ci95, `${side}.${r.horizon}.ci95 presence`); if (r.ci95) assert.ok(r.ci95[0] <= r.ci95[1]);
  checks++;
});

// analogs
const an = Q.findAnalogs(f);
assert.deepEqual(an.features_used, ref.analogs.features_used);
assert.deepEqual(an.matches.map((m) => [m.date, m.similarity, m.event]), ref.analogs.matches.map((m) => [m.date, m.similarity, m.event]), "analog matches"); checks++;
assert.deepEqual(Q.compareKeyDates(f).map((m) => [m.date, m.similarity]), ref.key_dates.map((m) => [m.date, m.similarity]), "key dates"); checks++;

// walk-forward and purged CV per strategy
for (const [id, e] of Object.entries(ref.walk_forward)) {
  const r = Q.walkForward(f, sf, id);
  if (e.error) { assert.ok(r.error, `${id} should error`); continue; }
  assert.deepEqual(r.folds.map((x) => [x.test, x.chosen_params]), e.folds.map((x) => [x.test, x.chosen_params]), `${id} folds`);
  for (const k of ["cagr", "sharpe", "max_drawdown", "trades", "exposure"]) { close(r.oos[k], e.oos[k], 1e-6, `${id}.oos.${k}`); close(r.in_sample_defaults.metrics[k], e.in_sample[k], 1e-6, `${id}.is.${k}`); }
  close(r.oos_vs_benchmark.sharpe_diff, e.sharpe_diff, 1e-6, `${id}.sharpe_diff`);
  const cv = Q.purgedCv(f, sf, id);
  assert.deepEqual(cv.folds.map((x) => x.chosen_params), e.cv_params, `${id} cv params`);
  close(cv.share_folds_beating_benchmark, e.cv_share, 1e-9, `${id} cv share`);
  checks++;
}

// signal scoreboard and one full report
const sb = Q.signalScoreboard(f, sf);
for (const e of ref.scoreboard) {
  const r = sb.find((x) => x.signal === e.signal);
  for (const k of ["n_events", "median", "hit_rate", "baseline_hit_rate", "excess_mean"]) close(r[k], e[k], 1e-9, `${e.signal}.${k}`);
  if (e.p_value !== null) assert.ok(Math.abs(r.p_value - e.p_value) < 0.06, `${e.signal} p ${r.p_value} vs ${e.p_value}`);
  assert.equal(r.active_now, e.active_now);
  checks++;
}
const rep = Q.signalReport(f, sf, "rsi_oversold");
assert.deepEqual(rep.turning_points, ref.report.turning_points, "turning points");
rep.stability_by_era.forEach((x, k) => { assert.deepEqual(x.period, ref.report.stability[k].period); close(x.excess_mean, ref.report.stability[k].excess_mean, 1e-9, "stability"); });
for (const h of Q.HORIZONS) close(rep.event_study.horizons[h].excess_mean, ref.report.excess[h], 1e-9, `report excess ${h}`);
checks++;

// chart markers, stock state, scanner
assert.deepEqual(Q.signalEvents(f, sf).map((e) => [e.date, e.type]), ref.events, "signal markers"); checks++;
const ss = Q.stateStats(f);
assert.equal(ss.state, ref.state.state);
for (const h of ["20", "60", "126"]) for (const k of ["n_samples", "median", "hit_rate"]) close(ss.stats[h][k], ref.state.stats[h][k], 1e-9, `state ${h} ${k}`);
checks++;
const bench = new Map(ref.bars.map((b) => [b.t, b.c]));
const scans = ref.scanner.symbols.map((s) => Q.scanSymbol(s.symbol, Q.technicalFrame(s.bars, s.symbol === ref.scanner.benchmark ? null : bench)));
const sum = Q.scanSummary(scans);
for (const row of ref.scanner.rows) { const r = sum.rows.find((x) => x.symbol === row.symbol); assert.deepEqual(r.flags, row.flags); close(r.bullish_evidence, row.bullish_evidence, 1e-9, "evidence"); close(r.rs63, row.rs63, 1e-6, "rs63"); }
for (const [k, v] of Object.entries(ref.scanner.flag_evidence)) { assert.equal(sum.flag_evidence[k].n, v.n, `scanner ${k} n`); close(sum.flag_evidence[k].excess_mean, v.excess_mean, 1e-9, `scanner ${k}`); }
checks++;
console.log(`ok: ${checks} groups of checks against the server's Python`);
