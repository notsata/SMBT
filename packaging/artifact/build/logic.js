// SMBT Pocket logic: NYSE session clock, Alpha Vantage payload parsers, indicators and the stock-state classifier.
// Pure functions (no DOM, no storage). Loaded by index.html and by logic.test.js in Node.
(function (root) {
  "use strict";

  // ---------- NYSE calendar (regular sessions; early closes not modelled) ----------
  const ymd = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const dow = (y, m, d) => new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  function nthWeekday(y, m, wd, n) { // n-th weekday (0=Sun) of month; n<0 counts from the end
    if (n > 0) { const first = dow(y, m, 1); return 1 + ((wd - first + 7) % 7) + 7 * (n - 1); }
    const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
    return last - ((dow(y, m, last) - wd + 7) % 7);
  }
  function easter(y) { // anonymous Gregorian computus
    const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25),
      g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4,
      l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    return [Math.floor((h + l - 7 * m + 114) / 31), ((h + l - 7 * m + 114) % 31) + 1];
  }
  function observed(y, m, d) { // Sat -> Fri, Sun -> Mon
    const w = dow(y, m, d), t = new Date(Date.UTC(y, m - 1, d + (w === 6 ? -1 : w === 0 ? 1 : 0)));
    return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
  }
  const holidayCache = {};
  function holidays(y) {
    if (holidayCache[y]) return holidayCache[y];
    const [em, ed] = easter(y);
    const gf = new Date(Date.UTC(y, em - 1, ed - 2));
    const s = new Set([
      observed(y, 1, 1), ymd(y, 1, nthWeekday(y, 1, 1, 3)), ymd(y, 2, nthWeekday(y, 2, 1, 3)),
      ymd(gf.getUTCFullYear(), gf.getUTCMonth() + 1, gf.getUTCDate()), ymd(y, 5, nthWeekday(y, 5, 1, -1)),
      observed(y, 7, 4), ymd(y, 9, nthWeekday(y, 9, 1, 1)), ymd(y, 11, nthWeekday(y, 11, 4, 4)), observed(y, 12, 25),
    ]);
    if (y >= 2022) s.add(observed(y, 6, 19));
    // Jan 1 on a Saturday is not observed (NYSE stays open Dec 31); observed() then yields a prior-year date this set never matches.
    // shortcut: one-off closures (national days of mourning) are not listed; such a day reads as a missed session.
    return (holidayCache[y] = s);
  }
  function isTradingDay(date) {
    const [y, m, d] = date.split("-").map(Number), w = dow(y, m, d);
    return w !== 0 && w !== 6 && !holidays(y).has(date);
  }
  function prevDay(date) {
    const [y, m, d] = date.split("-").map(Number), t = new Date(Date.UTC(y, m - 1, d - 1));
    return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
  }
  function nyParts(now) {
    const p = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(now).reduce((a, x) => ((a[x.type] = x.value), a), {});
    return { date: `${p.year}-${p.month}-${p.day}`, minutes: +p.hour * 60 + +p.minute };
  }
  /** {session: premarket|regular|afterhours|closed, today, lastCompleted, tradingDay} in New York time. */
  function sessionAt(now) {
    const { date, minutes } = nyParts(now);
    const td = isTradingDay(date);
    let session = "closed";
    if (td && minutes >= 240 && minutes < 570) session = "premarket";
    else if (td && minutes >= 570 && minutes < 960) session = "regular";
    else if (td && minutes >= 960 && minutes < 1200) session = "afterhours";
    let last = td && minutes >= 960 ? date : prevDay(date);
    while (!isTradingDay(last)) last = prevDay(last);
    return { session, today: date, tradingDay: td, lastCompleted: last };
  }
  /**
   * Status for an Alpha Vantage quote. AV reports only the trading day, not a trade timestamp, so a quote is
   * LIVE only when the viewer asked for the real-time entitlement, AV accepted it, and the session is open today.
   */
  function quoteStatus(latestDay, s, realtimeAccepted) {
    if (!latestDay) return ["UNAVAILABLE", "No quote returned"];
    const open = s.session === "regular" || s.session === "premarket" || s.session === "afterhours";
    if (open && latestDay === s.today) {
      return realtimeAccepted && s.session === "regular"
        ? ["LIVE", "Real-time entitlement accepted by Alpha Vantage (no per-trade timestamp is provided)"]
        : ["DELAYED", "Today's session; Alpha Vantage does not report the exact delay (free keys: delayed or end-of-day)"];
    }
    if (latestDay >= s.lastCompleted) return [open ? "DELAYED" : "CLOSED", open ? `Last close ${latestDay}; no print for today yet` : `Market closed; last session ${latestDay}`];
    return ["STALE", `Provider's latest day is ${latestDay}, but ${s.lastCompleted} has closed`];
  }

  // ---------- Alpha Vantage payloads (JSON or CSV, as the connector returns them) ----------
  function providerMessage(p) {
    if (p && typeof p === "object" && !Array.isArray(p)) for (const k of ["Error Message", "Information", "Note"]) if (typeof p[k] === "string") return p[k];
    if (typeof p === "string" && !p.includes(",") && p.trim()) return p.trim();
    return null;
  }
  function asData(p) { // payload -> object | csv rows | null
    if (typeof p === "string") {
      const t = p.trim();
      if (t.startsWith("{")) try { return JSON.parse(t); } catch { return null; }
      const lines = t.split(/\r?\n/).filter(Boolean);
      if (lines.length < 2 || !lines[0].includes(",")) return null;
      const head = lines[0].split(",").map((h) => h.trim());
      return lines.slice(1).map((l) => Object.fromEntries(l.split(",").map((v, i) => [head[i], v.trim()])));
    }
    return p && typeof p === "object" ? p : null;
  }
  const num = (v) => { const x = parseFloat(String(v ?? "").replace("%", "")); return Number.isFinite(x) ? x : null; };
  function parseQuote(payload) {
    const msg = providerMessage(payload);
    if (msg) throw new Error(msg);
    const d = asData(payload);
    let r = null;
    if (Array.isArray(d)) { const x = d[0] || {}; r = { symbol: x.symbol, open: x.open, high: x.high, low: x.low, price: x.price, volume: x.volume, day: x.latestDay, prev: x.previousClose, change: x.change, pct: x.changePercent }; }
    else if (d && d["Global Quote"]) { const q = d["Global Quote"]; r = { symbol: q["01. symbol"], open: q["02. open"], high: q["03. high"], low: q["04. low"], price: q["05. price"], volume: q["06. volume"], day: q["07. latest trading day"], prev: q["08. previous close"], change: q["09. change"], pct: q["10. change percent"] }; }
    if (!r || num(r.price) === null) return null; // AV answers an unknown symbol with an empty quote
    return { symbol: r.symbol, open: num(r.open), high: num(r.high), low: num(r.low), last: num(r.price), volume: num(r.volume), day: r.day, prevClose: num(r.prev), change: num(r.change), changePct: num(r.pct) === null ? null : num(r.pct) / 100 };
  }
  function parseDaily(payload) { // -> bars oldest first
    const msg = providerMessage(payload);
    if (msg) throw new Error(msg);
    const d = asData(payload);
    let rows = [];
    if (Array.isArray(d)) rows = d.map((x) => ({ t: x.timestamp, o: num(x.open), h: num(x.high), l: num(x.low), c: num(x.close), v: num(x.volume) }));
    else if (d) {
      const key = Object.keys(d).find((k) => k.startsWith("Time Series"));
      if (key) rows = Object.entries(d[key]).map(([t, x]) => ({ t, o: num(x["1. open"]), h: num(x["2. high"]), l: num(x["3. low"]), c: num(x["4. close"]), v: num(x["5. volume"]) }));
    }
    rows = rows.filter((b) => /^\d{4}-\d{2}-\d{2}$/.test(b.t || "") && b.c !== null && b.c > 0 && b.h >= b.l);
    rows.sort((a, b) => (a.t < b.t ? -1 : 1));
    return rows.filter((b, i) => i === 0 || b.t !== rows[i - 1].t);
  }

  // ---------- indicators (same definitions as the server's technical_frame) ----------
  const last = (a) => a[a.length - 1];
  function sma(x, n) { const out = Array(x.length).fill(null); let s = 0; for (let i = 0; i < x.length; i++) { s += x[i]; if (i >= n) s -= x[i - n]; if (i >= n - 1) out[i] = s / n; } return out; }
  function wilder(x, n) { // pandas ewm(alpha=1/n, adjust=False, min_periods=n): seeded with the first value, output from the n-th
    const out = Array(x.length).fill(null); let v = null, k = 0;
    for (let i = 0; i < x.length; i++) {
      if (x[i] === null) continue;
      v = v === null ? x[i] : v + (x[i] - v) / n;
      if (++k >= n) out[i] = v;
    }
    return out;
  }
  function rsi(c, n = 14) {
    const d = c.map((v, i) => (i ? v - c[i - 1] : null));
    const up = wilder(d.map((v) => (v === null ? null : Math.max(v, 0))), n), dn = wilder(d.map((v) => (v === null ? null : Math.max(-v, 0))), n);
    return up.map((u, i) => (u === null || dn[i] === null ? null : dn[i] === 0 ? 100 : 100 - 100 / (1 + u / dn[i])));
  }
  function std(a, ddof = 0) { const m = a.reduce((s, v) => s + v, 0) / a.length; return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - ddof)); }
  function indicators(bars) {
    const c = bars.map((b) => b.c), n = c.length, i = n - 1;
    const s20 = sma(c, 20), s50 = sma(c, 50), s200 = sma(c, 200), r = rsi(c, 14);
    const tr = bars.map((b, k) => (k ? Math.max(b.h - b.l, Math.abs(b.h - bars[k - 1].c), Math.abs(b.l - bars[k - 1].c)) : b.h - b.l));
    const atr = wilder(tr, 14);
    const win = c.slice(Math.max(0, n - 252));
    const hi = n >= 50 ? Math.max(...win) : null;
    const rets = c.slice(1).map((v, k) => Math.log(v / c[k]));
    const vols = bars.map((b) => b.v || 0), hasVol = vols.slice(-50).filter((v) => v > 0).length >= 40;
    const vAvg = n >= 50 ? vols.slice(-50).reduce((a, v) => a + v, 0) / 50 : null;
    let obvSlope = null;
    if (hasVol && n >= 21) { let o = 0; const obv = c.map((v, k) => (k ? (o += Math.sign(v - c[k - 1]) * vols[k]) : 0)); obvSlope = (obv[i] - obv[i - 20]) / vols.slice(-20).reduce((a, v) => a + v, 0); }
    const bbWin = c.slice(-20), mid = n >= 20 ? s20[i] : null, sd = n >= 20 ? std(bbWin) : null;
    const dist200Series = c.map((v, k) => (s200[k] ? v / s200[k] - 1 : null)).filter((v) => v !== null);
    const d200 = s200[i] ? c[i] / s200[i] - 1 : null;
    return {
      n, close: c[i], sma20: s20[i], sma50: s50[i], sma200: s200[i], dist200: d200,
      sma200_slope: s200[i] && s200[i - 20] ? s200[i] / s200[i - 20] - 1 : null,
      rsi14: r[i], atr_pct: atr[i] ? atr[i] / c[i] : null,
      bb_pctb: sd ? (c[i] - (mid - 2 * sd)) / (4 * sd) : null,
      hv20: rets.length >= 20 ? std(rets.slice(-20), 1) * Math.sqrt(252) : null,
      roc20: n > 20 ? c[i] / c[i - 20] - 1 : null, roc63: n > 63 ? c[i] / c[i - 63] - 1 : null,
      drawdown: hi ? c[i] / hi - 1 : null, highWindow: win.length,
      vol_ratio: hasVol && vAvg ? vols[i] / vAvg : null, obv_slope20: obvSlope,
      // percentile of today's distance from the 200-day within its own history (min 252 observations, as on the server)
      dist_pct: dist200Series.length >= 252 ? dist200Series.filter((v) => v <= d200).length / dist200Series.length : null,
      series: { sma50: s50, sma200: s200 },
    };
  }

  // ---------- stock state (server classify_row without divergence and relative-strength inputs) ----------
  const pct0 = (v) => `${Math.round(v * 100)}%`;
  function classify(x) {
    const { close, sma20, sma50, sma200, drawdown: dd, rsi14: r, sma200_slope: slope, dist_pct, dist200 } = x;
    if ([close, sma50, sma200, dd, r, slope].some((v) => v === null || v === undefined)) return ["neutral", ["Insufficient history: needs at least 220 daily sessions"]];
    const slopeUp = slope > 0, obvUp = (x.obv_slope20 ?? 0) > 0.05;
    if (dist_pct !== null && dist_pct >= 0.95 && r >= 75)
      return ["potential_exhaustion", [`Distance from 200-day (${(dist200 * 100).toFixed(1)}%) is in the top 5% of its own history`, `RSI ${r.toFixed(0)}`]];
    if (close < sma200 && !slopeUp && dd <= -0.15 && (sma50 < sma200 || dd <= -0.25)) {
      const why = ["Below a falling 200-day average", sma50 < sma200 ? "50-day below 200-day" : "Drawdown beyond 25%", `Drawdown ${pct0(dd)}`];
      if (obvUp && close > sma20) return ["possible_accumulation", [...why, "But: on-balance volume rising and price above the 20-day"]];
      return ["structural_decline", why];
    }
    if ((dd <= -0.15 || close < sma200) && obvUp && close > sma20 && close > sma50)
      return ["possible_accumulation", [`Drawdown ${pct0(dd)}`, "Price reclaimed short-term averages", "On-balance volume rising"]];
    if (slopeUp && dd >= -0.15 && dd <= -0.04 && close > sma200 * 0.97) {
      const why = ["200-day average still rising", `Pullback of ${pct0(dd)} from 52-week high`, `RSI ${r.toFixed(0)}`];
      if (x.vol_ratio !== null && x.vol_ratio < 1.5) why.push("Volume not abnormal (no distribution spike)");
      return ["normal_pullback", why];
    }
    if (slopeUp && close > sma50) return ["uptrend_intact", ["Above rising 200-day and 50-day averages", `Drawdown ${pct0(dd)}`]];
    return ["neutral", [`${close > sma200 ? "Above" : "Below"} the 200-day average (${(dist200 * 100).toFixed(1)}%)`, `Drawdown ${pct0(dd)}`, "No clear pullback, accumulation, decline or exhaustion pattern"]];
  }
  const STATE_LABELS = { potential_exhaustion: "Potential exhaustion", structural_decline: "Deeper structural decline", possible_accumulation: "Possible accumulation",
    normal_pullback: "Normal pullback", uptrend_intact: "Uptrend intact", neutral: "Neutral / no clear setup" };

  root.SMBT = { isTradingDay, sessionAt, quoteStatus, parseQuote, parseDaily, providerMessage, indicators, classify, STATE_LABELS, sma, rsi };
})(typeof window !== "undefined" ? window : globalThis);
