// Checks the phone page's logic.js against the server's Python implementation and Alpha Vantage's documented payload shapes.
// Run by backend/tests/test_pocket.py: node logic.test.js <logic.js> <ref.json written by the Python side>.
const assert = require("node:assert/strict");
require(process.argv[2]);
const S = globalThis.SMBT;
const ref = require(process.argv[3]);

// 1. NYSE calendar vs exchange_calendars XNYS, 2007-2030 (one-off closures are a documented gap)
const ONE_OFF = new Set(["2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09"]);
const bad = Object.entries(ref.trading).filter(([d, t]) => S.isTradingDay(d) !== t && !ONE_OFF.has(d));
assert.deepEqual(bad, [], "calendar mismatches");

// 2+3. indicators and state vs technical_frame / classify_row (divergences off, no relative strength)
const close = (a, b, tol, k) => {
  if (a === null || b === null) return assert.equal(a, b, `${k}: ${a} vs ${b}`);
  assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${k}: ${a} vs ${b}`);
};
let states = 0;
for (const c of ref.checks) {
  const x = S.indicators(ref.bars.slice(0, c.i + 1));
  for (const k of ["rsi14", "sma200", "dist200", "sma200_slope", "drawdown", "atr_pct", "bb_pctb", "hv20", "vol_ratio", "obv_slope20"]) close(x[k], c[k], 1e-6, `${k}@${c.i}`);
  close(x.dist_pct, c.dist_pct, 2e-3, `dist_pct@${c.i}`);
  assert.equal(S.classify(x)[0], c.state, `state@${c.i}`);
  states++;
}

// 4. payloads (shapes from Alpha Vantage's documentation; values are made up for the test)
const qJson = { "Global Quote": { "01. symbol": "SPY", "02. open": "100.0", "03. high": "101.5", "04. low": "99.5", "05. price": "101.0", "06. volume": "1000", "07. latest trading day": "2026-11-20", "08. previous close": "100.5", "09. change": "0.5", "10. change percent": "0.4975%" } };
const qCsv = "symbol,open,high,low,price,volume,latestDay,previousClose,change,changePercent\r\nSPY,100.0,101.5,99.5,101.0,1000,2026-11-20,100.5,0.5,0.4975%\r\n";
for (const p of [qJson, JSON.stringify(qJson), qCsv]) {
  const q = S.parseQuote(p);
  assert.equal(q.last, 101); assert.equal(q.day, "2026-11-20"); assert.equal(q.prevClose, 100.5); close(q.changePct, 0.004975, 1e-9, "pct");
}
assert.equal(S.parseQuote({ "Global Quote": {} }), null, "unknown symbol -> no quote");
// the connector's envelope (shape as returned by the Alpha Vantage claude.ai connector; values made up)
assert.equal(S.parseQuote({ result: qCsv }).last, 101, "{result: csv} quote");
assert.equal(S.parseQuote(JSON.stringify({ result: qCsv })).day, "2026-11-20", "{result} as JSON text");
assert.throws(() => S.parseDaily({ error: { type: "rate_limit", message: "The outputsize=full parameter value is a premium feature.", detail: "x" } }), /premium feature/);
assert.equal(S.providerMessage(JSON.stringify({ error: { type: "rate_limit", message: "25 requests per day" } })), "25 requests per day");
assert.equal(S.providerMessage({ result: "Thank you for using Alpha Vantage! Please subscribe, or wait." }), "Thank you for using Alpha Vantage! Please subscribe, or wait.");
assert.equal(S.providerMessage({ result: qCsv }), null);
assert.equal(S.parseQuote({ result: "symbol,open,high,low,price,volume,latestDay,previousClose,change,changePercent\r\n" }), null, "unknown symbol: header only");
assert.throws(() => S.parseQuote({ Information: "We have detected your API key ... 25 requests per day." }), /25 requests/);
assert.throws(() => S.parseDaily({ "Error Message": "Invalid API call." }), /Invalid API call/);
const dJson = { "Meta Data": {}, "Time Series (Daily)": { "2026-11-20": { "1. open": "1", "2. high": "2", "3. low": "0.5", "4. close": "1.5", "5. volume": "10" }, "2026-11-19": { "1. open": "1", "2. high": "1.2", "3. low": "0.9", "4. close": "1", "5. volume": "9" } } };
const dCsv = "timestamp,open,high,low,close,volume\r\n2026-11-20,1,2,0.5,1.5,10\r\n2026-11-19,1,1.2,0.9,1,9\r\n2026-11-18,1,0.5,0.9,1,9\r\n";
for (const p of [dJson, dCsv, { result: dCsv }]) {
  const b = S.parseDaily(p);
  assert.deepEqual(b.map((x) => x.t), ["2026-11-19", "2026-11-20"], "oldest first, invalid high<low row dropped");
}

// 4b. Twelve Data connector shapes (as returned by the claude.ai connector; values made up)
const tdQ = { result: "symbol;name;exchange;mic_code;currency;datetime;timestamp;last_quote_at;open;high;low;close;volume;previous_close;change;percent_change;is_market_open\nSPY;Some ETF;NYSE;ARCX;USD;2026-11-20;1795185000;1795208340;100.0;101.5;99.5;101.0;1000;100.5;0.5;0.4975;false" };
const tq = S.parseQuote(tdQ);
assert.equal(tq.last, 101); assert.equal(tq.day, "2026-11-20"); assert.equal(tq.name, "Some ETF"); assert.equal(tq.quoteTs, 1795208340000); close(tq.changePct, 0.004975, 1e-9, "td pct");
assert.throws(() => S.parseQuote({ result: "Error: **symbol** or **figi** parameter is missing or invalid." }), /symbol/);
assert.throws(() => S.parseDaily({ result: "Error: You have run out of API credits for the current minute." }), /API credits/);
const tdD = S.parseDaily({ result: "datetime;open;high;low;close;volume\n2026-11-20;1;2;0.5;1.5;10\n2026-11-19;1;1.2;0.9;1;9\n" });
assert.deepEqual(tdD.map((x) => x.t), ["2026-11-19", "2026-11-20"]);

// 5. session clock and status (instants in UTC; New York is UTC-5 in late November)
const at = (iso) => S.sessionAt(new Date(iso));
assert.deepEqual(at("2026-11-22T17:00:00Z"), { session: "closed", today: "2026-11-22", tradingDay: false, lastCompleted: "2026-11-20" });
assert.equal(at("2026-11-23T15:00:00Z").session, "regular");
assert.equal(at("2026-11-23T15:00:00Z").lastCompleted, "2026-11-20");
assert.equal(at("2026-11-23T21:30:00Z").lastCompleted, "2026-11-23");
assert.equal(at("2026-11-26T16:00:00Z").session, "closed", "Thanksgiving");
assert.equal(at("2026-11-23T12:00:00Z").session, "premarket");
const reg = at("2026-11-23T15:00:00Z"), sun = at("2026-11-22T17:00:00Z");
assert.equal(S.quoteStatus("2026-11-23", reg, false)[0], "DELAYED");
assert.equal(S.quoteStatus("2026-11-23", reg, true)[0], "LIVE");
assert.equal(S.quoteStatus("2026-11-20", reg, true)[0], "DELAYED", "no print today yet");
assert.equal(S.quoteStatus("2026-11-20", sun, false)[0], "CLOSED");
assert.equal(S.quoteStatus("2026-11-19", sun, false)[0], "STALE");
assert.equal(S.quoteStatus(null, sun, false)[0], "UNAVAILABLE");
const now = Date.parse("2026-11-23T15:00:00Z");
assert.equal(S.quoteStatus("2026-11-23", reg, false, now - 30e3, now)[0], "LIVE", "30 s old quote");
assert.equal(S.quoteStatus("2026-11-23", reg, false, now - 15 * 60e3, now)[0], "DELAYED", "15 min old quote");
assert.equal(S.quoteStatus("2026-11-23", reg, false, now - 45 * 60e3, now)[0], "STALE", "45 min without a quote in session");
assert.equal(S.quoteStatus("2026-11-20", sun, false, now - 30e3, now)[0], "CLOSED", "weekend: day-based");
console.log(`ok: calendar ${Object.keys(ref.trading).length} weekdays, ${states} indicator/state checkpoints, parsers, session/status`);
