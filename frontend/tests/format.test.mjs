import assert from "node:assert/strict";
import { test } from "node:test";
import { daysAgo, num, pct, signedClass } from "../lib/format.ts";

test("pct formats fractions and never fabricates a number for missing data", () => {
  assert.equal(pct(0.1234), "12.3%");
  assert.equal(pct(0.05, 1, true), "+5.0%");
  assert.equal(pct(-0.05, 1, true), "-5.0%");
  assert.equal(pct(null), "—");
  assert.equal(pct(Number.NaN), "—");
  assert.equal(pct(undefined), "—");
});

test("num and signedClass handle missing and signed values", () => {
  assert.equal(num(null), "—");
  assert.equal(signedClass(0.1), "text-up");
  assert.equal(signedClass(-0.1), "text-down");
  assert.equal(signedClass(0.1, true), "text-down");
  assert.equal(signedClass(null), "text-muted");
});

test("daysAgo", () => {
  assert.equal(daysAgo(null), null);
  assert.ok(daysAgo("2000-01-01") > 9000);
});
