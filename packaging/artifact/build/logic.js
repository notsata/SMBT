// SMBT phone page: NYSE session clock, quote status and connector payload parsers (analysis lives in quant.js).
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
   * Status for a quote. With the provider's own quote time (Twelve Data) the age decides during regular hours:
   * at most 2 minutes old is LIVE, at most 20 minutes DELAYED, older STALE. Without it (Alpha Vantage reports only the
   * trading day) a quote is LIVE only when the real-time entitlement was requested and accepted.
   */
  function quoteStatus(latestDay, s, realtimeAccepted, quoteTsMs, nowMs) {
    if (!latestDay) return ["UNAVAILABLE", "No quote returned"];
    const open = s.session === "regular" || s.session === "premarket" || s.session === "afterhours";
    if (quoteTsMs && s.session === "regular" && latestDay === s.today) {
      const age = Math.max(0, Math.round((nowMs - quoteTsMs) / 1000)), mins = Math.round(age / 60);
      if (age <= 120) return ["LIVE", `Last quote ${age} s old`];
      if (age <= 1200) return ["DELAYED", `Last quote ${mins} min old`];
      return ["STALE", `No new quote for ${mins} min during regular hours`];
    }
    if (open && latestDay === s.today) {
      return realtimeAccepted && s.session === "regular"
        ? ["LIVE", "Real-time entitlement accepted by Alpha Vantage (no per-trade timestamp is provided)"]
        : ["DELAYED", "Today's session; Alpha Vantage does not report the exact delay (free keys: delayed or end-of-day)"];
    }
    if (latestDay >= s.lastCompleted) return [open ? "DELAYED" : "CLOSED", open ? `Last close ${latestDay}; no print for today yet` : `Market closed; last session ${latestDay}`];
    return ["STALE", `Provider's latest day is ${latestDay}, but ${s.lastCompleted} has closed`];
  }

  // ---------- connector payloads ----------
  // Both connectors wrap data as {"result": "<text>"}: Alpha Vantage as comma-separated values (or AV JSON), Twelve Data as
  // semicolon-separated values. Failures: AV {"error": {"type", "message", "detail"}}, Twelve Data {"result": "Error: ..."}.
  function unwrap(p) {
    if (typeof p === "string") {
      const t = p.trim();
      if (t.startsWith("{")) try { return unwrap(JSON.parse(t)); } catch { /* not JSON: treat as text */ }
      return t;
    }
    if (p && typeof p === "object" && !Array.isArray(p)) {
      if (typeof p.result === "string" || (p.result && typeof p.result === "object")) return unwrap(p.result);
      if (p.error) return { "Error Message": typeof p.error === "string" ? p.error : p.error.message || p.error.detail || "Alpha Vantage reported an error" };
    }
    return p;
  }
  const isCsv = (t) => { const head = t.split(/\r?\n/)[0]; return /[,;]/.test(head) && !head.includes(" "); }; // a header alone = no rows
  function providerMessage(p) {
    const u = unwrap(p);
    if (u && typeof u === "object" && !Array.isArray(u)) for (const k of ["Error Message", "Information", "Note"]) if (typeof u[k] === "string") return u[k];
    if (typeof u === "string" && u && !isCsv(u)) return u.slice(0, 400);
    return null;
  }
  function asData(p) { // payload -> object | csv rows | null
    const u = unwrap(p);
    if (typeof u === "string") {
      if (!isCsv(u)) return null;
      const lines = u.split(/\r?\n/).filter(Boolean);
      const sep = lines[0].includes(";") ? ";" : ",";
      const head = lines[0].split(sep).map((h) => h.trim());
      return lines.slice(1).map((l) => Object.fromEntries(l.split(sep).map((v, i) => [head[i], v.trim()])));
    }
    return u && typeof u === "object" ? u : null;
  }
  const num = (v) => { const x = parseFloat(String(v ?? "").replace("%", "")); return Number.isFinite(x) ? x : null; };
  function parseQuote(payload) {
    const msg = providerMessage(payload);
    if (msg) throw new Error(msg);
    const d = asData(payload);
    let r = null;
    if (Array.isArray(d) && d[0] && "last_quote_at" in d[0]) { // Twelve Data
      const x = d[0];
      r = { symbol: x.symbol, name: x.name, exchange: x.exchange, open: x.open, high: x.high, low: x.low, price: x.close, volume: x.volume, day: x.datetime, prev: x.previous_close, change: x.change, pct: x.percent_change, ts: x.last_quote_at };
    } else if (Array.isArray(d)) { const x = d[0] || {}; r = { symbol: x.symbol, open: x.open, high: x.high, low: x.low, price: x.price, volume: x.volume, day: x.latestDay, prev: x.previousClose, change: x.change, pct: x.changePercent }; }
    else if (d && d["Global Quote"]) { const q = d["Global Quote"]; r = { symbol: q["01. symbol"], open: q["02. open"], high: q["03. high"], low: q["04. low"], price: q["05. price"], volume: q["06. volume"], day: q["07. latest trading day"], prev: q["08. previous close"], change: q["09. change"], pct: q["10. change percent"] }; }
    if (!r || num(r.price) === null) return null; // AV answers an unknown symbol with an empty quote
    return { symbol: r.symbol, name: r.name || null, exchange: r.exchange || null, open: num(r.open), high: num(r.high), low: num(r.low), last: num(r.price), volume: num(r.volume), day: r.day,
      prevClose: num(r.prev), change: num(r.change), changePct: num(r.pct) === null ? null : num(r.pct) / 100, quoteTs: num(r.ts) === null ? null : num(r.ts) * 1000 };
  }
  function parseDaily(payload) { // -> bars oldest first
    const msg = providerMessage(payload);
    if (msg) throw new Error(msg);
    const d = asData(payload);
    let rows = [];
    if (Array.isArray(d)) rows = d.map((x) => ({ t: x.datetime ?? x.timestamp, o: num(x.open), h: num(x.high), l: num(x.low), c: num(x.close), v: num(x.volume) }));
    else if (d) {
      const key = Object.keys(d).find((k) => k.startsWith("Time Series"));
      if (key) rows = Object.entries(d[key]).map(([t, x]) => ({ t, o: num(x["1. open"]), h: num(x["2. high"]), l: num(x["3. low"]), c: num(x["4. close"]), v: num(x["5. volume"]) }));
    }
    rows = rows.filter((b) => /^\d{4}-\d{2}-\d{2}$/.test(b.t || "") && b.c !== null && b.c > 0 && b.h >= b.l);
    rows.sort((a, b) => (a.t < b.t ? -1 : 1));
    return rows.filter((b, i) => i === 0 || b.t !== rows[i - 1].t);
  }

  root.SMBT = { isTradingDay, sessionAt, quoteStatus, parseQuote, parseDaily, providerMessage };
})(typeof window !== "undefined" ? window : globalThis);
