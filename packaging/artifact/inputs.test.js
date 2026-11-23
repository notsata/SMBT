// Checks quant.js's market-wide inputs (breadth, VIX sentiment, stock/bond, point-in-time macro, Fear & Greed,
// macro risk) and the scores built on them against the server's Python. Usage: node inputs.test.js <quant.js> <ref.json>
const assert = require("node:assert/strict");
require(process.argv[2]);
const Q = globalThis.SMBTQ;
const ref = require(process.argv[3]);
const close = (a, b, tol, what) => {
  const na = (x) => x === null || x === undefined || Number.isNaN(x);
  if (na(a) || na(b)) return assert.ok(na(a) && na(b), `${what}: ${a} vs ${b}`);
  assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${what}: ${a} vs ${b}`);
};
const toMap = (rows) => new Map(rows.map(([d, v]) => [d, v]));
const f = Q.technicalFrame(ref.bars);
Q.augment(f, { members: Object.fromEntries(Object.entries(ref.members).map(([k, v]) => [k, toMap(v)])), minNames: ref.min_names, breadthFlags: true,
  vix: toMap(ref.vix), tlt: toMap(ref.tlt), macro: Object.fromEntries(Object.entries(ref.macro).map(([k, v]) => [k, v.map(([date, value]) => ({ date, value }))])) });
const sf = Q.scoreFrame(f);
let n = 0;
for (const [k, rows] of Object.entries(ref.cols)) for (const [i, v] of rows) { close(typeof f[k][i] === "boolean" ? +f[k][i] : f[k][i], v, 1e-6, `${k}@${i}`); n++; }
for (const [k, idx] of Object.entries(ref.flags)) { assert.deepEqual(f[k].flatMap((x, i) => (x ? [i] : [])), idx, `flag ${k}`); n++; }
assert.deepEqual(sf.regime, ref.regime, "regime with breadth and VIX"); n++;
for (const [k, rows] of Object.entries(ref.scores)) for (const [i, v] of rows) { close(sf[k][i], v, 1e-6, `${k}@${i}`); n++; }
const an = Q.findAnalogs(f);
assert.deepEqual(an.features_used, ref.analogs.features_used, "analog features");
assert.deepEqual(an.matches.map((m) => [m.date, m.similarity]), ref.analogs.matches, "analog matches"); n++;
console.log(`ok: ${n} checks of market-wide inputs against the server's Python`);
