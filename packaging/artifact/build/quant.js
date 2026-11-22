// SMBT quant engine for the phone page: a line-by-line port of backend/app/quant and backend/app/backtest.
// Columns are plain arrays (NaN = missing, booleans for flags). backend/tests/test_pocket.py checks every function
// against the Python implementation. Random resampling (bootstrap CIs, random-entry nulls) uses a seeded generator
// of its own, so those numbers agree with the server's statistically, not digit for digit.
(function (root) {
  "use strict";
  const ANN = 252;
  const ok = (v) => typeof v === "number" && v === v;
  const nan = (n) => new Array(n).fill(NaN);
  const add = (a, b) => a.map((x, i) => x + b[i]);
  const sub = (a, b) => a.map((x, i) => x - b[i]);
  const mul = (a, b) => a.map((x, i) => x * b[i]);
  const div = (a, b) => a.map((x, i) => x / b[i]);
  const where = (a, cond) => a.map((x, i) => (cond[i] ? x : NaN));
  const fillna = (a, v) => a.map((x) => (ok(x) ? x : v));
  const and = (...xs) => xs[0].map((_, i) => xs.every((x) => x[i] === true));
  const or = (...xs) => xs[0].map((_, i) => xs.some((x) => x[i] === true));
  const not = (a) => a.map((x) => !x);
  const gt = (a, b) => a.map((x, i) => x > (Array.isArray(b) ? b[i] : b)); // NaN compares false, as in pandas
  const ge = (a, b) => a.map((x, i) => x >= (Array.isArray(b) ? b[i] : b));
  const lt = (a, b) => a.map((x, i) => x < (Array.isArray(b) ? b[i] : b));
  const le = (a, b) => a.map((x, i) => x <= (Array.isArray(b) ? b[i] : b));
  const num = (b) => b.map((x) => (x ? 1 : 0));
  const clip = (a, lo, hi) => a.map((x) => (ok(x) ? Math.min(hi, Math.max(lo, x)) : x));
  function shift(a, k = 1) { const n = a.length, o = nan(n); for (let i = 0; i < n; i++) { const j = i - k; if (j >= 0 && j < n) o[i] = a[j]; } return o; }
  const diff = (a, k = 1) => sub(a, shift(a, k));
  const pctChange = (a, k = 1) => a.map((x, i) => (i >= k ? x / a[i - k] - 1 : NaN));
  const mean = (v) => { let s = 0, n = 0; for (const x of v) if (ok(x)) { s += x; n++; } return n ? s / n : NaN; };
  const std = (v, ddof = 1) => { const w = v.filter(ok); if (w.length <= ddof) return NaN; const m = w.reduce((s, x) => s + x, 0) / w.length; return Math.sqrt(w.reduce((s, x) => s + (x - m) ** 2, 0) / (w.length - ddof)); };
  const sum = (v) => v.reduce((s, x) => (ok(x) ? s + x : s), 0);
  function percentile(v, p) { const s = v.filter(ok).sort((a, b) => a - b); if (!s.length) return NaN; const pos = (s.length - 1) * p / 100, lo = Math.floor(pos); return s[lo] + (s[Math.min(lo + 1, s.length - 1)] - s[lo]) * (pos - lo); }
  const median = (v) => percentile(v, 50);

  // ---------- rolling / expanding / ewm (pandas semantics) ----------
  function rolling(a, n, minP, fn) { // fn(valid values) for windows with >= minP valid values
    const o = nan(a.length);
    for (let i = 0; i < a.length; i++) {
      const w = []; for (let j = Math.max(0, i - n + 1); j <= i; j++) if (ok(a[j])) w.push(a[j]);
      if (w.length >= minP) o[i] = fn(w);
    }
    return o;
  }
  function rollSum(a, n, minP = n) { // running sums: O(n)
    const o = nan(a.length); let s = 0, c = 0;
    for (let i = 0; i < a.length; i++) {
      if (ok(a[i])) { s += a[i]; c++; }
      const j = i - n; if (j >= 0 && ok(a[j])) { s -= a[j]; c--; }
      if (c >= minP) o[i] = s;
    }
    return o;
  }
  function rollMean(a, n, minP = n) {
    const o = nan(a.length); let s = 0, c = 0;
    for (let i = 0; i < a.length; i++) {
      if (ok(a[i])) { s += a[i]; c++; }
      const j = i - n; if (j >= 0 && ok(a[j])) { s -= a[j]; c--; }
      if (c >= minP && c > 0) o[i] = s / c;
    }
    return o;
  }
  const rollStd = (a, n, ddof = 1, minP = n) => rolling(a, n, minP, (w) => std(w, ddof));
  const rollMax = (a, n, minP = n) => rolling(a, n, minP, (w) => Math.max(...w));
  const rollMin = (a, n, minP = n) => rolling(a, n, minP, (w) => Math.min(...w));
  function ewm(a, alpha, minP) { // pandas ewm(adjust=False, ignore_na=False)
    const o = nan(a.length); let w = NaN, oldWt = 1, nobs = 0;
    for (let i = 0; i < a.length; i++) {
      const x = a[i], obs = ok(x); nobs += obs;
      if (ok(w)) { oldWt *= 1 - alpha; if (obs) { if (w !== x) w = (oldWt * w + alpha * x) / (oldWt + alpha); oldWt = 1; } }
      else if (obs) w = x;
      if (nobs >= minP) o[i] = w;
    }
    return o;
  }
  const wilder = (a, n) => ewm(a, 1 / n, n);
  const ema = (a, span) => ewm(a, 2 / (span + 1), span);
  function expandingPct(a, minP = 252) { // rank(pct=True, average ties) of today's value among values so far
    const vals = [...new Set(a.filter(ok))].sort((x, y) => x - y), pos = new Map(vals.map((v, i) => [v, i + 1]));
    const bit = new Float64Array(vals.length + 1), o = nan(a.length); let cnt = 0;
    const put = (i) => { for (; i < bit.length; i += i & -i) bit[i]++; }, pre = (i) => { let s = 0; for (; i > 0; i -= i & -i) s += bit[i]; return s; };
    for (let i = 0; i < a.length; i++) {
      if (!ok(a[i])) continue;
      const r = pos.get(a[i]); put(r); cnt++;
      if (cnt >= minP) { const less = pre(r - 1), eq = pre(r) - less; o[i] = (less + (eq + 1) / 2) / cnt; }
    }
    return o;
  }
  function recent(flag, n) { const o = new Array(flag.length).fill(false); let last = -1e9; for (let i = 0; i < flag.length; i++) { if (flag[i]) last = i; o[i] = i - last < n; } return o; }

  // ---------- technical frame ----------
  function rsi(c, n = 14) {
    const d = diff(c);
    const up = wilder(d.map((x) => (ok(x) ? Math.max(x, 0) : NaN)), n), dn = wilder(d.map((x) => (ok(x) ? Math.max(-x, 0) : NaN)), n);
    return up.map((u, i) => (!ok(u) ? NaN : dn[i] === 0 ? 100 : 100 - 100 / (1 + u / dn[i])));
  }
  function trueRange(b) {
    const pc = shift(b.c);
    return b.c.map((c, i) => {
      const parts = [b.h[i] - b.l[i], Math.abs(b.h[i] - pc[i]), Math.abs(b.l[i] - pc[i])];
      return parts.every(ok) ? Math.max(...parts) : Math.abs(c - pc[i]);
    });
  }
  function adx(b, n = 14) {
    const h = b.h.map((x, i) => (ok(x) ? x : b.c[i])), l = b.l.map((x, i) => (ok(x) ? x : b.c[i]));
    const upm = diff(h), dnm = diff(l).map((x) => -x);
    const pdm = upm.map((u, i) => (u > dnm[i] && u > 0 ? u : 0)), ndm = dnm.map((d, i) => (d > upm[i] && d > 0 ? d : 0));
    const a = wilder(trueRange(b), n).map((x) => (x === 0 ? NaN : x));
    const pdi = div(wilder(pdm, n), a).map((x) => 100 * x), ndi = div(wilder(ndm, n), a).map((x) => 100 * x);
    const dx = pdi.map((p, i) => { const s = p + ndi[i]; return s === 0 ? NaN : 100 * Math.abs(p - ndi[i]) / s; });
    return wilder(dx, n);
  }
  function pivots(x, k, kind) {
    const n = x.length, o = new Array(n).fill(false);
    for (let i = k; i < n - k; i++) {
      let e = x[i], full = true;
      for (let j = i - k; j <= i + k; j++) { if (!ok(x[j])) { full = false; break; } e = kind === "low" ? Math.min(e, x[j]) : Math.max(e, x[j]); }
      if (full && x[i] === e) o[i] = true;
    }
    return o;
  }
  function divergences(t, price, osc, k = 5, maxGap = 60, minGap = 8, loTh = 40, hiTh = 60) {
    const n = price.length, bull = new Array(n).fill(false), bear = new Array(n).fill(false), events = [];
    for (const [kind, out] of [["low", bull], ["high", bear]]) {
      const piv = pivots(price, k, kind).flatMap((v, i) => (v ? [i] : []));
      for (let m = 0; m + 1 < piv.length; m++) {
        const a = piv[m], b = piv[m + 1];
        if (!(b - a >= minGap && b - a <= maxGap) || b + k >= n || !ok(osc[a]) || !ok(osc[b])) continue;
        const hit = kind === "low" ? price[b] < price[a] && osc[b] > osc[a] && osc[a] < loTh : price[b] > price[a] && osc[b] < osc[a] && osc[a] > hiTh;
        if (!hit) continue;
        out[b + k] = true;
        events.push({ type: kind === "low" ? "bullish" : "bearish", confirmed: t[b + k], pivot1: t[a], pivot2: t[b], price: [price[a], price[b]], osc: [osc[a], osc[b]] });
      }
    }
    return { bull, bear, events };
  }
  /** bars: [{t,o,h,l,c,v}] oldest first; benchClose: Map date -> close (optional, for relative strength). */
  function technicalFrame(bars, benchClose) {
    const b = { t: bars.map((x) => x.t), o: bars.map((x) => x.o ?? NaN), h: bars.map((x) => x.h ?? NaN), l: bars.map((x) => x.l ?? NaN), c: bars.map((x) => x.c), v: bars.map((x) => x.v ?? NaN) };
    const c = b.c, n = c.length, f = { t: b.t, n, bars: b, close: c };
    for (const k of [5, 20, 50, 100, 200]) f["sma" + k] = rollMean(c, k);
    f.dist200 = div(c, f.sma200).map((x) => x - 1);
    f.dist50 = div(c, f.sma50).map((x) => x - 1);
    f.sma200_slope = pctChange(f.sma200, 20);
    f.rsi14 = rsi(c, 14); f.rsi2 = rsi(c, 2);
    const fast = ema(c, 12), slow = ema(c, 26);
    f.macd = sub(fast, slow); f.macd_signal = ema(f.macd, 9); f.macd_hist = sub(f.macd, f.macd_signal);
    const lo14 = rollMin(f.rsi14, 14), hi14 = rollMax(f.rsi14, 14);
    f.stochrsi_k = rollMean(f.rsi14.map((r, i) => { const d = hi14[i] - lo14[i]; return d === 0 ? NaN : (r - lo14[i]) / d * 100; }), 3);
    f.bb_mid = rollMean(c, 20);
    const sd = rollStd(c, 20, 0);
    f.bb_upper = add(f.bb_mid, sd.map((x) => 2 * x)); f.bb_lower = sub(f.bb_mid, sd.map((x) => 2 * x));
    f.bb_pctb = c.map((x, i) => (x - f.bb_lower[i]) / (f.bb_upper[i] - f.bb_lower[i]));
    f.adx = adx(b);
    f.atr14 = wilder(trueRange(b), 14);
    f.atr_pct = div(f.atr14, c);
    f.ext_atr = div(sub(c, f.sma50), f.atr14);
    f.hv20 = rollStd(diff(c.map(Math.log)), 20).map((x) => x * Math.sqrt(ANN));
    f.ret1 = pctChange(c, 1); f.roc20 = pctChange(c, 20); f.roc63 = pctChange(c, 63); f.roc126 = pctChange(c, 126);
    f.hi252 = rollMax(c, 252, 50); f.lo252 = rollMin(c, 252, 50);
    f.drawdown = div(c, f.hi252).map((x) => x - 1);
    const vol = b.v, hasVol = ge(rollSum(vol.map((x) => (x > 0 ? 1 : 0)), 50), 40);
    f.vol_ratio = where(div(vol, rollMean(vol, 50)), hasVol);
    let s = 0;
    const obvRaw = c.map((x, i) => { const d = i ? Math.sign(x - c[i - 1]) : NaN; s += (ok(d) ? d : 0) * (ok(vol[i]) ? vol[i] : 0); return s; });
    f.obv = where(obvRaw, hasVol);
    f.obv_slope20 = where(div(diff(f.obv, 20), rollSum(vol, 20)), hasVol);
    const rd = divergences(b.t, c, f.rsi14), md = divergences(b.t, c, f.macd_hist, 5, 60, 8, 0, 0);
    f.rsi_bull_div = rd.bull; f.rsi_bear_div = rd.bear; f.macd_bull_div = md.bull; f.macd_bear_div = md.bear; f.rsi_div_events = rd.events;
    f.vp_bear_div = and(gt(f.roc20, 0.03), lt(f.obv_slope20, -0.05));
    f.vp_bull_div = and(lt(f.roc20, -0.03), gt(f.obv_slope20, 0.05));
    const lc = c.map(Math.log);
    f.r2_63 = c.map((_, i) => { // squared correlation of log price with time over 63 sessions
      if (i < 62) return NaN;
      let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0;
      for (let j = 0; j < 63; j++) { const y = lc[i - 62 + j]; sx += j; sy += y; sxx += j * j; syy += y * y; sxy += j * y; }
      const cov = sxy - sx * sy / 63, vx = sxx - sx * sx / 63, vy = syy - sy * sy / 63;
      return vy > 0 ? (cov * cov) / (vx * vy) : NaN;
    });
    if (benchClose) {
      f.rs_ratio = c.map((x, i) => x / (benchClose.get(b.t[i]) ?? NaN));
      f.rs63 = pctChange(f.rs_ratio, 63); f.rs126 = pctChange(f.rs_ratio, 126);
    }
    return f;
  }
  const cache = new WeakMap(); // percentile series per frame (computed once)
  function P(f, name, minP = 252) {
    let m = cache.get(f); if (!m) cache.set(f, (m = {}));
    const key = name + ":" + minP;
    return m[key] || (m[key] = expandingPct(f[name] || nan(f.n), minP));
  }
  const col = (f, name) => f[name] || nan(f.n);
  const flag = (f, name) => f[name] || new Array(f.n).fill(false);

  // ---------- regime and scores ----------
  const REGIMES = { strong_bull: "Strong bull market", bull_correction: "Bull market correction", sideways: "Sideways consolidation", early_bear: "Early bear market",
    bear_capitulation: "Bear market capitulation", recovery: "Recovery / accumulation", distribution: "Potential distribution / market top" };
  function breadthDiv(f) { const p50 = col(f, "pct_above_50"), d = diff(p50, 63); return p50.map((p, i) => (ok(p) ? Math.min(1, Math.max(0, -d[i] / 30)) * (f.drawdown[i] > -0.03 ? 1 : 0) : NaN)); }
  function classifyRegime(f) {
    const c = f.close, dd = f.drawdown, slope = f.sma200_slope, above = gt(c, f.sma200), vixP = P(f, "vix");
    const capit = and(le(dd, -0.2), or(ge(vixP, 0.9), lt(f.rsi14, 30), ge(col(f, "pct_oversold"), 40), le(col(f, "pct_above_50"), 15)));
    const wasBear = recent(or(le(dd, -0.2), and(not(above), lt(slope, 0))), 126);
    const sma50Up = gt(diff(f.sma50, 10), 0);
    const recovery = and(wasBear, le(dd, -0.08), gt(c, f.sma50), sma50Up, gt(f.roc20, 0.04));
    const earlyBear = and(not(above), le(slope, 0), le(dd, -0.1));
    const warnings = or(recent(or(flag(f, "rsi_bear_div"), flag(f, "macd_bear_div")), 15), recent(flag(f, "vp_bear_div"), 10), ge(breadthDiv(f), 0.5));
    const distribution = and(above, gt(dd, -0.05), ge(P(f, "dist200"), 0.85), warnings);
    const strong = and(above, gt(slope, 0.005), gt(f.sma50, f.sma200), gt(dd, -0.05));
    const corr = and(gt(slope, -0.002), le(dd, -0.05), gt(dd, -0.2));
    const conds = [capit, recovery, earlyBear, distribution, strong, corr], names = ["bear_capitulation", "recovery", "early_bear", "distribution", "strong_bull", "bull_correction"];
    return c.map((_, i) => (!ok(f.sma200[i]) || !ok(dd[i]) ? null : names[conds.findIndex((x) => x[i])] || "sideways"));
  }
  function regimeReasons(f, i = f.n - 1) {
    const pc = (v, d = 1) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
    return [`Close ${f.close[i] > f.sma200[i] ? "above" : "below"} 200-day average (${pc(f.dist200[i])})`,
      `200-day average ${f.sma200_slope[i] > 0 ? "rising" : "falling"} (${pc(f.sma200_slope[i], 2)} over 20d)`,
      `Drawdown from 52-week high ${(f.drawdown[i] * 100).toFixed(1)}%`, `RSI(14) ${f.rsi14[i].toFixed(0)}`];
  }
  const C = (id, label, category, fn, detail) => ({ id, label, category, fn, detail });
  const inv = (a) => a.map((x) => 1 - x);
  const BOTTOM = [
    C("rsi_low", "RSI(14) low vs history", "technical", (f) => inv(P(f, "rsi14")), "1 - percentile of RSI(14)"),
    C("drawdown", "Depth of drawdown", "technical", (f) => inv(P(f, "drawdown")), "1 - percentile of drawdown from 52w high"),
    C("below_200", "Distance below 200-day", "technical", (f) => inv(P(f, "dist200")), "1 - percentile of distance from 200d MA"),
    C("bb_low", "Bollinger %B low", "technical", (f) => inv(P(f, "bb_pctb")), "1 - percentile of %B(20,2)"),
    C("bull_div", "Bullish RSI/MACD divergence (10d)", "technical", (f) => num(recent(or(flag(f, "rsi_bull_div"), flag(f, "macd_bull_div")), 10)), "confirmed pivot divergence"),
    C("capitulation_vol", "Capitulation volume (5d)", "technical", (f) => where(num(recent(and(ge(col(f, "vol_ratio"), 2), lt(f.ret1, -0.02)), 5)), col(f, "vol_ratio").map(ok)), "volume >= 2x 50d avg on a >2% down day"),
    C("breadth_low", "Few members above 50-day", "breadth", (f) => col(f, "pct_above_50").map((x) => 1 - x / 100), "1 - % above 50d MA"),
    C("oversold_share", "Share of members oversold", "breadth", (f) => clip(col(f, "pct_oversold").map((x) => x / 50), 0, 1), "% members RSI<30, /50"),
    C("mcclellan_low", "McClellan Oscillator low", "breadth", (f) => inv(P(f, "mcclellan_osc")), "1 - percentile"),
    C("nhnl_low", "Net new lows", "breadth", (f) => inv(P(f, "nhnl_pct")), "1 - percentile of (NH-NL)%"),
    C("zweig", "Zweig breadth thrust (20d)", "breadth", (f) => where(num(recent(flag(f, "zweig_thrust"), 20)), col(f, "zweig_ema").map(ok)), "thrust fired"),
    C("washout", "90% down day (10d)", "breadth", (f) => where(num(recent(flag(f, "washout_day"), 10)), col(f, "zweig_ema").map(ok)), ">=90% decliners"),
    C("vix_high", "VIX elevated", "sentiment", (f) => P(f, "vix"), "percentile of VIX"),
    C("vix_backwardation", "VIX term structure inverted", "sentiment", (f) => P(f, "vix_term"), "percentile of VIX/VIX3M"),
    C("fear", "Fear & Greed (fear)", "sentiment", (f) => col(f, "fear_greed").map((x) => 1 - x / 100), "1 - reconstructed F&G/100"),
    C("hv_high", "Realized volatility high", "sentiment", (f) => P(f, "hv20"), "percentile of 20d HV"),
    C("credit_easing", "Credit stress easing from high", "macro", (f) => where(mul(P(f, "credit_spread"), num(lt(col(f, "credit_chg20"), 0))), col(f, "credit_spread").map(ok)), "spread percentile x (spread falling over 20d)"),
  ];
  const TOP = [
    C("extension", "Extension above 200-day", "technical", (f) => P(f, "dist200"), "percentile of distance from 200d MA"),
    C("rsi_high", "RSI(14) high vs history", "technical", (f) => P(f, "rsi14"), "percentile of RSI(14)"),
    C("atr_extension", "Stretch above 50-day in ATRs", "technical", (f) => P(f, "ext_atr"), "percentile of (close-50d)/ATR"),
    C("bear_div", "Bearish divergence near highs (15d)", "technical", (f) => num(and(recent(or(flag(f, "rsi_bear_div"), flag(f, "macd_bear_div")), 15), gt(f.drawdown, -0.05))), "confirmed pivot divergence"),
    C("vp_div", "Price up on falling OBV (10d)", "technical", (f) => where(num(recent(flag(f, "vp_bear_div"), 10)), col(f, "obv").map(ok)), "OBV divergence"),
    C("breadth_div", "Breadth divergence at highs", "breadth", breadthDiv, "near high while % above 50d fell vs 63d ago"),
    C("overbought_share", "Share of members overbought", "breadth", (f) => clip(col(f, "pct_overbought").map((x) => x / 40), 0, 1), "% members RSI>70, /40"),
    C("nhnl_div", "Net new highs weak near high", "breadth", (f) => where(num(and(gt(f.drawdown, -0.03), lt(rollMean(col(f, "nhnl_pct"), 20), 0))), col(f, "nhnl_pct").map(ok)), "near index high with net new lows"),
    C("vix_low", "VIX complacency", "sentiment", (f) => inv(P(f, "vix")), "1 - percentile of VIX"),
    C("contango", "Steep VIX contango", "sentiment", (f) => inv(P(f, "vix_term")), "1 - percentile of VIX/VIX3M"),
    C("greed", "Fear & Greed (greed)", "sentiment", (f) => col(f, "fear_greed").map((x) => x / 100), "reconstructed F&G/100"),
    C("uninversion", "Yield curve un-inverting", "macro", (f) => where(num(flag(f, "curve_uninversion")), col(f, "curve_10y2y").map(ok)), "10y-2y > 0 after >= 60 inverted days in past year"),
    C("credit_widening", "Credit spreads widening", "macro", (f) => P(f, "credit_chg63"), "percentile of 63d spread change"),
    C("fed_tightening", "Fed tightening", "macro", (f) => clip(col(f, "fed_chg252").map((x) => x / 2), 0, 1), "12m fed funds change / 200bp"),
    C("sahm", "Unemployment rising (Sahm)", "macro", (f) => clip(col(f, "sahm").map((x) => x / 0.5), 0, 1), "Sahm gap / 0.5pp"),
    C("valuation", "Valuation stretched", "macro", (f) => P(f, "mcap_gdp", 20), "percentile of market cap / GDP"),
  ];
  const BOTTOM_W = { default: { technical: 0.35, breadth: 0.25, sentiment: 0.30, macro: 0.10 }, strong_bull: { technical: 0.50, breadth: 0.20, sentiment: 0.25, macro: 0.05 },
    bull_correction: { technical: 0.40, breadth: 0.25, sentiment: 0.30, macro: 0.05 }, early_bear: { technical: 0.25, breadth: 0.30, sentiment: 0.30, macro: 0.15 },
    bear_capitulation: { technical: 0.20, breadth: 0.35, sentiment: 0.35, macro: 0.10 }, recovery: { technical: 0.30, breadth: 0.35, sentiment: 0.25, macro: 0.10 } };
  const TOP_W = { default: { technical: 0.30, breadth: 0.25, sentiment: 0.25, macro: 0.20 }, strong_bull: { technical: 0.30, breadth: 0.25, sentiment: 0.30, macro: 0.15 },
    distribution: { technical: 0.25, breadth: 0.35, sentiment: 0.25, macro: 0.15 }, bear_capitulation: { technical: 0.40, breadth: 0.20, sentiment: 0.20, macro: 0.20 },
    early_bear: { technical: 0.30, breadth: 0.20, sentiment: 0.20, macro: 0.30 } };
  function weightedScore(f, comps, regime, weights) {
    const cf = Object.fromEntries(comps.map((c) => [c.id, clip(c.fn(f).map(Number), 0, 1)]));
    const cats = [...new Set(comps.map((c) => c.category))].sort();
    const score = nan(f.n), coverage = nan(f.n), catMean = Object.fromEntries(cats.map((k) => [k, nan(f.n)]));
    for (let i = 0; i < f.n; i++) {
      const w = weights[regime[i]] || weights.default;
      let num_ = 0, den = 0, avail = 0;
      for (const k of cats) {
        const m = mean(comps.filter((c) => c.category === k).map((c) => cf[c.id][i]));
        catMean[k][i] = m;
        if (ok(m)) { num_ += m * w[k]; den += w[k]; }
      }
      for (const c of comps) avail += ok(cf[c.id][i]) ? 1 : 0;
      coverage[i] = avail / comps.length;
      if (den > 0 && coverage[i] >= 0.25) score[i] = num_ / den * 100;
    }
    return { score, coverage, cf, catMean };
  }
  function trendConfidence(f) {
    const up = f.sma200_slope.map((s) => s >= 0), d = up.map((u) => (u ? 1 : -1));
    const seq = [f.close, f.sma20, f.sma50, f.sma100, f.sma200];
    const align = f.close.map((_, i) => seq.slice(0, -1).reduce((s, a, k) => s + ((a[i] - seq[k + 1][i]) * d[i] > 0 ? 1 : 0), 0) / 4);
    const side = rollMean(f.close.map((c, i) => ((c - f.sma50[i]) * d[i] > 0 ? 1 : 0)), 63, 40);
    const parts = { adx: clip(f.adx.map((x) => (x - 10) / 30), 0, 1), ma_alignment: align, r2_63: f.r2_63, persistence: side };
    if (f.pct_above_200 && f.pct_above_200.some(ok)) parts.breadth_confirm = f.pct_above_200.map((x, i) => (up[i] ? x / 100 : 1 - x / 100));
    const keys = Object.keys(parts);
    const trend = f.close.map((_, i) => { const v = keys.map((k) => parts[k][i]).filter(ok); return v.length >= 3 ? v.reduce((s, x) => s + x, 0) / v.length * 100 : NaN; });
    return { trend, dir: up.map((u) => (u ? "up" : "down")), parts };
  }
  function scoreFrame(f) {
    const regime = classifyRegime(f);
    const b = weightedScore(f, BOTTOM, regime, BOTTOM_W), t = weightedScore(f, TOP, regime, TOP_W), tc = trendConfidence(f);
    return { regime, bottom: b.score, bottom_cov: b.coverage, top: t.score, top_cov: t.coverage, trend: tc.trend, trend_dir: tc.dir, tc: tc.parts, b: b.cf, tcomp: t.cf };
  }
  function explain(sf, i, side) {
    const comps = side === "bottom" ? BOTTOM : TOP, weights = side === "bottom" ? BOTTOM_W : TOP_W, cf = side === "bottom" ? sf.b : sf.tcomp;
    const w = weights[sf.regime[i]] || weights.default;
    const items = comps.map((c) => { const v = cf[c.id][i]; return { id: c.id, label: c.label, category: c.category, value: ok(v) ? Math.round(v * 1000) / 1000 : null, category_weight: w[c.category], detail: c.detail, available: ok(v) }; });
    items.sort((a, b) => -(a.value || 0) + (b.value || 0));
    const s = sf[side][i];
    return { score: ok(s) ? Math.round(s * 10) / 10 : null, coverage: Math.round(sf[side + "_cov"][i] * 100) / 100, regime: sf.regime[i], components: items };
  }

  // ---------- empirical probabilities ----------
  function rng(seed) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  const randInt = (r, n) => Math.floor(r() * n);
  const HORIZONS = [5, 10, 20, 60, 126, 252];
  const fwdReturn = (c, h) => c.map((x, i) => (i + h < c.length ? c[i + h] / x - 1 : NaN));
  function fwdMinReturn(c, h) { return c.map((x, i) => { if (i + h >= c.length) return NaN; let m = Infinity; for (let j = i + 1; j <= i + h; j++) m = Math.min(m, c[j]); return m / x - 1; }); }
  function episodes(mask, gap) { const pos = mask.flatMap((v, i) => (v ? [i] : [])); const ep = []; let id = 0; pos.forEach((p, k) => { if (k && p - pos[k - 1] > gap) id++; ep.push(id); }); return ep; }
  function clusterCi(values, ep, seed = 7, nBoot = 1000) {
    const ids = [...new Set(ep)]; if (ids.length < 5) return null;
    const groups = ids.map((id) => values.filter((_, k) => ep[k] === id)), r = rng(seed), stats = [];
    for (let b = 0; b < nBoot; b++) { let s = 0, n = 0; for (let j = 0; j < ids.length; j++) { const g = groups[randInt(r, ids.length)]; for (const v of g) { s += v; n++; } } stats.push(s / n); }
    return [percentile(stats, 2.5), percentile(stats, 97.5)];
  }
  function describe(values, ep) {
    const keep = values.map(ok), v = values.filter(ok);
    if (!v.length) return { n: 0 };
    const out = { n: v.length, mean: mean(v), median: median(v), hit_rate: v.filter((x) => x > 0).length / v.length, pct: Object.fromEntries([5, 10, 25, 50, 75, 90, 95].map((p) => [String(p), percentile(v, p)])) };
    if (ep) {
      const e = ep.filter((_, k) => keep[k]);
      out.episodes = new Set(e).size;
      out.mean_ci95 = clusterCi(v, e);
      out.hit_rate_ci95 = clusterCi(v.map((x) => (x > 0 ? 1 : 0)), e);
    }
    return out;
  }
  const bucket = (s, w = 10) => s.map((x) => (ok(x) ? Math.floor(Math.min(99.999, Math.max(0, x)) / w) : NaN));
  function conditional(score, outcome, current, horizon, width = 10) {
    const b = bucket(score, width), cb = Math.floor(Math.min(Math.max(current, 0), 99.999) / width);
    const mask = b.map((x, i) => x === cb && ok(outcome[i]));
    const res = describe(outcome.filter((_, i) => mask[i]), episodes(mask, horizon));
    res.bucket = [cb * width, cb * width + width];
    res.baseline = describe(outcome.filter(ok));
    res.sufficient = (res.episodes || 0) >= 8;
    return res;
  }
  function walkForwardProbability(score, label, h, width = 10, minN = 30) {
    const b = bucket(score, width), nb = 100 / width, cn = new Array(nb).fill(0), cy = new Array(nb).fill(0), n = score.length;
    let tn = 0, ty = 0;
    const p = nan(n), base = nan(n);
    for (let i = 0; i < n; i++) {
      if (i >= h && ok(label[i - h]) && ok(b[i - h])) { tn++; ty += label[i - h]; cn[b[i - h]]++; cy[b[i - h]] += label[i - h]; }
      if (!ok(b[i])) continue;
      base[i] = tn > 0 ? ty / tn : NaN;
      p[i] = cn[b[i]] >= minN ? cy[b[i]] / cn[b[i]] : base[i];
    }
    return { p, base, label };
  }
  function evaluateOos(wf, startAfter = 756) {
    const rows = []; for (let i = startAfter; i < wf.p.length; i++) if (ok(wf.p[i]) && ok(wf.base[i]) && ok(wf.label[i])) rows.push(i);
    if (rows.length < 100) return { n: rows.length, brier_skill: null, note: "insufficient out-of-sample history" };
    const bm = mean(rows.map((i) => (wf.p[i] - wf.label[i]) ** 2)), bb = mean(rows.map((i) => (wf.base[i] - wf.label[i]) ** 2));
    return { n: rows.length, brier_model: bm, brier_base_rate: bb, brier_skill: bb > 0 ? 1 - bm / bb : null };
  }
  function scoreProbabilities(sf, close) {
    const out = { kind: "empirical", method: "score-bucket conditional frequency; walk-forward with purged labels" };
    for (const [side, defs] of [["bottom", [20, 60, 126].map((h) => ["positive_return", h, fwdReturn(close, h)])], ["top", [60, 126, 252].map((h) => ["correction_10pct", h, fwdMinReturn(close, h)])]]) {
      const cur = sf[side][sf[side].length - 1];
      out[side] = defs.map(([name, h, cont]) => {
        const lab = cont.map((x) => (ok(x) ? (name === "positive_return" ? (x > 0 ? 1 : 0) : (x <= -0.1 ? 1 : 0)) : NaN));
        if (!ok(cur)) return { event: name, horizon: h, available: false };
        const wf = walkForwardProbability(sf[side], lab, h), cond = conditional(sf[side], lab, cur, h), L = wf.p.length - 1;
        return { event: name, horizon: h, available: true, probability: ok(wf.p[L]) ? wf.p[L] : null, base_rate: ok(wf.base[L]) ? wf.base[L] : null,
          bucket: cond.bucket, episodes: cond.episodes || 0, days: cond.n || 0, ci95: cond.mean_ci95 || null, sufficient: cond.sufficient, oos: evaluateOos(wf) };
      });
    }
    return out;
  }

  // ---------- historical analogs ----------
  const FEATURES = { momentum: ["dist200", "roc63"], drawdown: ["drawdown"], breadth: ["pct_above_50", "nhnl_pct"], sentiment: ["vix", "vix_term", "fear_greed"],
    volatility: ["hv20"], valuation: ["mcap_gdp"], credit: ["credit_spread", "credit_chg63"], liquidity: ["net_liq_chg63"], rates: ["curve_10y2y", "real_10y"] };
  const EVENTS = [["Dot-com peak", "2000-01-14", "2000-09-01"], ["Dot-com bear market", "2000-09-02", "2002-06-30"], ["2002 bear-market bottom", "2002-07-01", "2003-03-31"],
    ["Pre-GFC peak", "2007-06-01", "2007-12-31"], ["Financial crisis crash", "2008-01-01", "2009-02-28"], ["GFC bottom / early recovery", "2009-03-01", "2009-06-30"],
    ["2010 flash-crash correction", "2010-04-23", "2010-07-02"], ["2011 debt-ceiling correction", "2011-07-22", "2011-10-31"], ["2015-16 China/oil correction", "2015-08-01", "2016-02-29"],
    ["Feb 2018 volatility shock", "2018-01-26", "2018-04-30"], ["Q4 2018 selloff", "2018-09-20", "2018-12-31"], ["Pre-COVID peak", "2020-01-15", "2020-02-21"],
    ["COVID crash", "2020-02-22", "2020-04-30"], ["2022 inflation bear market", "2022-01-03", "2022-10-31"], ["2023 regional bank stress", "2023-03-08", "2023-03-31"],
    ["Aug 2024 yen carry unwind", "2024-07-16", "2024-08-15"], ["2025 tariff shock", "2025-02-19", "2025-04-30"]];
  const KEY_DATES = [["2000-03-24", "Dot-com peak", "top"], ["2002-10-09", "2002 bear-market low", "bottom"], ["2007-10-09", "2007 pre-GFC peak", "top"], ["2009-03-09", "GFC low", "bottom"],
    ["2011-10-03", "2011 correction low", "bottom"], ["2016-02-11", "2016 correction low", "bottom"], ["2018-09-20", "2018 peak", "top"], ["2018-12-24", "2018 low", "bottom"],
    ["2020-02-19", "Pre-COVID peak", "top"], ["2020-03-23", "COVID low", "bottom"], ["2022-01-03", "2022 peak", "top"], ["2022-10-12", "2022 bear-market low", "bottom"],
    ["2025-04-08", "2025 tariff-shock low", "bottom"]];
  const eventLabel = (d) => (EVENTS.find(([, a, b]) => a <= d && d <= b) || [null])[0];
  function fingerprint(f) {
    const cols = {};
    for (const [g, names] of Object.entries(FEATURES)) for (const nm of names) if (f[nm] && f[nm].filter(ok).length > 300) cols[`${g}:${nm}`] = P(f, nm);
    return cols;
  }
  function outcomes(c, i) {
    const out = Object.fromEntries(HORIZONS.map((h) => [String(h), i + h < c.length ? c[i + h] / c[i] - 1 : null]));
    const m = fwdMinReturn(c.slice(i, i + 127), 126)[0];
    out.max_drawdown_126 = ok(m) ? m : null;
    return out;
  }
  function findAnalogs(f, k = 10, excludeRecent = 126, decluster = 40, minFeatures = 4) {
    const X = fingerprint(f), last = f.n - 1, used = Object.keys(X).filter((c) => ok(X[c][last]));
    if (used.length < minFeatures) return { matches: [], features_used: used, note: `only ${used.length} features available today` };
    const need = Math.max(minFeatures, Math.ceil(0.6 * used.length)), sims = [];
    for (let i = 0; i < Math.max(0, f.n - excludeRecent); i++) {
      let s = 0, m = 0; for (const c of used) if (ok(X[c][i])) { s += (X[c][i] - X[c][last]) ** 2; m++; }
      if (m >= need) sims.push([i, 1 - Math.sqrt(s / m)]);
    }
    sims.sort((a, b) => b[1] - a[1]);
    const picks = [];
    for (const [i] of sims) { if (picks.every((p) => Math.abs(i - p) > decluster)) { picks.push(i); if (picks.length === k) break; } }
    const matches = picks.map((i) => ({ date: f.t[i], similarity: Math.round(sims.find((s) => s[0] === i)[1] * 1000) / 1000, event: eventLabel(f.t[i]),
      features: Object.fromEntries(used.map((c) => [c, { then: ok(X[c][i]) ? Math.round(X[c][i] * 1000) / 1000 : null, now: Math.round(X[c][last] * 1000) / 1000 }])), forward: outcomes(f.close, i) }));
    const dist = {}, uncond = {};
    for (const h of HORIZONS) {
      const v = matches.map((m) => m.forward[String(h)]).filter((x) => x !== null);
      dist[String(h)] = v.length ? describe(v) : { n: 0 };
      uncond[String(h)] = describe(fwdReturn(f.close, h));
    }
    return { as_of: f.t[last], features_used: used, matches, outcome_distribution: dist, unconditional: uncond };
  }
  function compareKeyDates(f) {
    const X = fingerprint(f), cols = Object.keys(X), last = f.n - 1, out = [];
    for (const [ds, name, kind] of KEY_DATES) {
      let i = f.t.findIndex((t) => t >= ds);
      if (i < 0 || (Date.parse(f.t[i]) - Date.parse(ds)) / 864e5 > 5 || i > f.n - 21) continue;
      const common = cols.filter((c) => ok(X[c][i]) && ok(X[c][last]));
      if (common.length < 3) continue;
      const sim = 1 - Math.sqrt(mean(common.map((c) => (X[c][i] - X[c][last]) ** 2)));
      out.push({ date: f.t[i], name, kind, similarity: Math.round(sim * 1000) / 1000, features_compared: common.length, forward: outcomes(f.close, i) });
    }
    return out.sort((a, b) => b.similarity - a.similarity);
  }

  // ---------- backtest engine ----------
  function periodReturns(b) {
    const valid = b.o.filter(ok).length / b.o.length;
    if (valid > 0.98) { const o = b.o.map((x, i) => (ok(x) ? x : b.c[i])); return { r: o.map((x, i) => (i + 1 < o.length ? o[i + 1] / x - 1 : NaN)), mode: "next_open" }; }
    return { r: b.c.map((x, i) => (i + 1 < b.c.length ? b.c[i + 1] / x - 1 : NaN)), mode: "next_close" };
  }
  /** bars sliced [a, z) of a frame's bars; target aligned to the same rows. Returns rows a..z-2 (last has no forward return). */
  function run(b, target, a = 0, z = b.c.length, costBps = 3) {
    const sl = (x) => x.slice(a, z), bb = { o: sl(b.o), c: sl(b.c) }, { r, mode } = periodReturns(bb), tg = sl(target).map((x) => (ok(x) ? Math.min(1, Math.max(0, x)) : NaN));
    const n = r.length - 1, w = [], ret = [], asset = [], cost = [];
    for (let i = 0; i < n; i++) {
      const wi = i ? (ok(tg[i - 1]) ? tg[i - 1] : 0) : 0, prev = i ? w[i - 1] : 0, turn = Math.abs(wi - (i ? prev : 0));
      w.push(wi); asset.push(r[i]); cost.push(turn * costBps / 1e4); ret.push(wi * r[i] + (1 - wi) * 0 - turn * costBps / 1e4);
    }
    let e = 1, be = 1;
    const equity = ret.map((x) => (e *= 1 + x)), bench = asset.map((x) => (be *= 1 + (ok(x) ? x : 0)));
    return { start: a, weight: w, asset_ret: asset, ret, cost, equity, bench_equity: bench, mode };
  }
  const maxDrawdown = (eq) => { let pk = -Infinity, m = 0; for (const x of eq) { pk = Math.max(pk, x); m = Math.min(m, x / pk - 1); } return eq.length ? m : NaN; };
  function tradeStats(ret, w) {
    const trades = []; let cur = null;
    for (let i = 0; i < w.length; i++) { if (w[i] > 0) { if (cur === null) cur = 1; cur *= 1 + (ok(ret[i]) ? ret[i] : 0); } else if (cur !== null) { trades.push(cur - 1); cur = null; } }
    if (cur !== null) trades.push(cur - 1);
    if (!trades.length) return { trades: 0 };
    const wins = trades.filter((x) => x > 0), losses = trades.filter((x) => x <= 0), ls = sum(losses);
    return { trades: trades.length, win_rate: wins.length / trades.length, avg_trade: mean(trades), median_trade: median(trades),
      profit_factor: ls < 0 ? sum(wins) / -ls : null, avg_days_in_trade: w.filter((x) => x > 0).length / trades.length };
  }
  function metrics(retIn, weight) {
    const keep = retIn.map(ok), ret = retIn.filter(ok);
    if (ret.length < 20) return { n_days: ret.length };
    let e = 1; const eq = ret.map((x) => (e *= 1 + x)), yrs = ret.length / ANN;
    const sd = std(ret), neg = ret.filter((x) => x < 0), down = std(neg) * Math.sqrt(ANN), cagr = eq[eq.length - 1] > 0 ? eq[eq.length - 1] ** (1 / yrs) - 1 : -1, mdd = maxDrawdown(eq);
    const out = { n_days: ret.length, years: Math.round(yrs * 100) / 100, total_return: eq[eq.length - 1] - 1, cagr, volatility: sd * Math.sqrt(ANN),
      sharpe: sd > 0 ? mean(ret) / sd * Math.sqrt(ANN) : null, sortino: down > 0 ? mean(ret) * ANN / down : null, max_drawdown: mdd, calmar: mdd < 0 ? cagr / Math.abs(mdd) : null };
    if (weight) { const w = weight.filter((_, i) => keep[i]).map((x) => (ok(x) ? x : 0)); out.exposure = mean(w); Object.assign(out, tradeStats(ret, w)); }
    return out;
  }
  function blockBootstrapDiff(x, y, block = 20, nBoot = 1000, seed = 11) {
    const keep = x.map((v, i) => ok(v) && ok(y[i])); x = x.filter((_, i) => keep[i]); y = y.filter((_, i) => keep[i]);
    const n = x.length; if (n < block * 10) return {};
    const sh = (v) => { const s = std(v, 0); return s > 0 ? mean(v) / s * Math.sqrt(ANN) : 0; }, r = rng(seed), nb = Math.ceil(n / block), ds = [], dm = [];
    for (let k = 0; k < nBoot; k++) {
      const xs = [], ys = [];
      for (let j = 0; j < nb && xs.length < n; j++) { const s = randInt(r, n - block + 1); for (let q = 0; q < block && xs.length < n; q++) { xs.push(x[s + q]); ys.push(y[s + q]); } }
      ds.push(sh(xs) - sh(ys)); dm.push((mean(xs) - mean(ys)) * ANN);
    }
    return { sharpe_diff: sh(x) - sh(y), sharpe_diff_ci95: [percentile(ds, 2.5), percentile(ds, 97.5)], p_sharpe_diff_le_0: ds.filter((v) => v <= 0).length / nBoot,
      ann_return_diff: (mean(x) - mean(y)) * ANN, ann_return_diff_ci95: [percentile(dm, 2.5), percentile(dm, 97.5)], block, n_boot: nBoot };
  }
  function regimeSplit(ret, bench, bull) {
    const out = {};
    for (const [name, m] of [["bull", bull], ["bear", bull.map((x) => !x)]]) {
      const idx = m.flatMap((v, i) => (v ? [i] : []));
      if (idx.length < 60) { out[name] = { n_days: idx.length }; continue; }
      const r = idx.map((i) => ret[i]), br = idx.map((i) => (ok(bench[i]) ? bench[i] : 0)), sr = std(r), sb = std(br);
      out[name] = { n_days: idx.length, ann_return: mean(r) * ANN, bench_ann_return: mean(br) * ANN, sharpe: sr > 0 ? mean(r) / sr * Math.sqrt(ANN) : null, bench_sharpe: sb > 0 ? mean(br) / sb * Math.sqrt(ANN) : null };
    }
    return out;
  }

  // ---------- strategies ----------
  const holdAfter = (entries, days) => recent(entries.map((x) => x === true), days).map(Number);
  function latch(enter, exit, start = 0) { let s = start; return enter.map((e, i) => { if (exit[i]) s = 0; else if (e) s = 1; return s; }); }
  const STRATEGIES = {
    buy_hold: { name: "Buy and hold", description: "Always 100% invested (benchmark).", fn: (f) => new Array(f.n).fill(1), grid: {}, defaults: {} },
    trend200: { name: "200-day trend filter", description: "Long above the 200-day average, cash below (with a +/- band to cut whipsaws).",
      fn: (f, sf, p) => latch(f.close.map((c, i) => c > f.sma200[i] * (1 + p.band)), f.close.map((c, i) => c < f.sma200[i] * (1 - p.band)), 1), grid: { band: [0, 0.01, 0.02] }, defaults: { band: 0.01 } },
    bottom_score: { name: "Bottom score entries", description: "Cash by default; buy when Bottom Opportunity Score >= threshold and hold N sessions.",
      fn: (f, sf, p) => holdAfter(ge(sf.bottom, p.threshold), p.hold), grid: { threshold: [60, 65, 70, 75, 80], hold: [20, 60, 126] }, defaults: { threshold: 70, hold: 60 } },
    top_overlay: { name: "Top-risk de-risking", description: "Long by default; go to cash when Top Risk Score >= threshold, back in when it falls by `rearm`.",
      fn: (f, sf, p) => latch(ge(sf.top, p.threshold), lt(sf.top, p.threshold - p.rearm), 0).map((x) => 1 - x), grid: { threshold: [60, 65, 70, 75, 80], rearm: [10, 20] }, defaults: { threshold: 70, rearm: 10 } },
    score_overlay: { name: "Combined score overlay", description: "Top-risk de-risking, overridden to long for 60 sessions after a high Bottom score.",
      fn: (f, sf, p) => { const off = latch(ge(sf.top, p.top), lt(sf.top, p.top - 10), 0).map((x) => 1 - x), on = holdAfter(ge(sf.bottom, p.bottom), 60); return off.map((x, i) => Math.max(x, on[i])); },
      grid: { top: [65, 70, 75, 80], bottom: [60, 70, 80] }, defaults: { top: 70, bottom: 70 } },
    rsi2: { name: "RSI(2) mean reversion", description: "Connors-style: buy RSI(2) < entry above the 200-day; exit on a close above the 5-day average.",
      fn: (f, sf, p) => latch(and(lt(f.rsi2, p.entry), gt(f.close, f.sma200)), gt(f.close, f.sma5), 0), grid: { entry: [5, 10, 15] }, defaults: { entry: 10 } },
    zweig: { name: "Zweig breadth thrust", description: "Buy on a Zweig Breadth Thrust and hold N sessions.", needs: "breadth" },
    vix_spike: { name: "VIX spike entries", description: "Buy when VIX is above its historical percentile threshold; hold N sessions.", needs: "vix" },
  };
  function gridPoints(grid) { let pts = [{}]; for (const [k, vals] of Object.entries(grid)) pts = pts.flatMap((p) => vals.map((v) => ({ ...p, [k]: v }))); return pts; }
  const objective = (r) => { const x = r.filter(ok); const s = std(x); return x.length > 60 && s > 0 ? mean(x) / s * Math.sqrt(ANN) : -Infinity; };
  function warmupStart(f, sf) { const i = f.close.findIndex((_, k) => ok(f.sma200[k]) && ok(sf.bottom[k]) && ok(sf.top[k])); return i >= 0 ? i : Math.min(f.n - 1, 252); }
  const pick = (a, idx) => idx.map((i) => a[i]);
  function walkForward(f, sf, id, trainYears = 6, testYears = 2, costBps = 3) {
    const s = STRATEGIES[id];
    if (s.needs) return { strategy: id, error: `${s.name} needs ${s.needs} data, which this page has no source for.` };
    const runs = (gridPoints(s.grid).length ? gridPoints(s.grid) : [s.defaults]).map((p) => [p, run(f.bars, s.fn(f, sf, p), 0, f.n, costBps)]);
    const start = warmupStart(f, sf), idx = [];
    for (let i = start; i < f.n - 1; i++) idx.push(i);
    if (idx.length < (trainYears + testYears) * ANN) return { strategy: id, error: `Need ${trainYears + testYears} years after warm-up; have ${(idx.length / ANN).toFixed(1)}.` };
    const folds = [], oosTarget = nan(f.n);
    let trEnd = trainYears * ANN;
    while (trEnd < idx.length) {
      const teEnd = Math.min(trEnd + testYears * ANN, idx.length), tr = idx.slice(0, trEnd), te = idx.slice(trEnd, teEnd);
      let best = null;
      for (const [p, res] of runs) {
        const trades = tradeStats(pick(res.ret, tr), pick(res.weight, tr)).trades || 0;
        const obj = id === "buy_hold" || trades >= 3 ? objective(pick(res.ret, tr)) : -Infinity;
        if (!best || obj > best[0]) best = [obj, p, res];
      }
      let [bestObj, bestP, bestRes] = best;
      if (!Number.isFinite(bestObj)) { bestP = s.defaults; bestRes = (runs.find(([p]) => JSON.stringify(p) === JSON.stringify(bestP)) || runs[0])[1]; }
      const target = s.fn(f, sf, bestP);
      for (const i of te) oosTarget[i] = target[i];
      folds.push({ train: [f.t[tr[0]], f.t[tr[tr.length - 1]]], test: [f.t[te[0]], f.t[te[te.length - 1]]], chosen_params: bestP, train_sharpe: Number.isFinite(bestObj) ? bestObj : null,
        test_metrics: metrics(pick(bestRes.ret, te), pick(bestRes.weight, te)), test_benchmark: metrics(pick(bestRes.asset_ret, te)) });
      trEnd = teEnd;
    }
    const a = idx[trainYears * ANN], z = idx[idx.length - 1] + 1, oos = run(f.bars, oosTarget.map((x) => (ok(x) ? x : 0)), a, z, costBps);
    const bull = oos.ret.map((_, k) => f.close[a + k] > f.sma200[a + k]);
    const full = run(f.bars, s.fn(f, sf, s.defaults), start, f.n, costBps);
    const step = Math.max(1, Math.floor(oos.equity.length / 600)), eq = [];
    for (let k = 0; k < oos.equity.length; k += step) eq.push({ t: f.t[a + k], equity: oos.equity[k], bench_equity: oos.bench_equity[k] });
    if (eq[eq.length - 1].t !== f.t[a + oos.equity.length - 1]) eq.push({ t: f.t[a + oos.equity.length - 1], equity: oos.equity[oos.equity.length - 1], bench_equity: oos.bench_equity[oos.equity.length - 1] });
    return { strategy: id, name: s.name, description: s.description, folds, oos_period: [f.t[a], f.t[a + oos.ret.length - 1]],
      oos: metrics(oos.ret, oos.weight), oos_benchmark: metrics(oos.asset_ret), oos_vs_benchmark: blockBootstrapDiff(oos.ret, oos.asset_ret),
      oos_by_market_state: regimeSplit(oos.ret, oos.asset_ret, bull), equity: eq,
      assumptions: { execution: oos.mode, cost_bps_per_side: costBps, cash_yield: "0% (no T-bill source on this page)", train_years: trainYears, test_years: testYears, objective: "train-window Sharpe (min 3 trades)" },
      in_sample_defaults: { params: s.defaults, metrics: metrics(full.ret, full.weight), warning: "Full-period result with fixed default params. Not out-of-sample; do not use as a performance estimate." } };
  }
  function purgedCv(f, sf, id, k = 5, purge = 126, embargo = 20, costBps = 3) {
    const s = STRATEGIES[id];
    if (s.needs) return { strategy: id, error: `needs ${s.needs} data` };
    const runs = (gridPoints(s.grid).length ? gridPoints(s.grid) : [s.defaults]).map((p) => [p, run(f.bars, s.fn(f, sf, p), 0, f.n, costBps)]);
    const idx = []; for (let i = warmupStart(f, sf); i < f.n - 1; i++) idx.push(i);
    const n = idx.length; if (n < k * ANN) return { strategy: id, error: "insufficient history for purged CV" };
    const bounds = Array.from({ length: k + 1 }, (_, i) => Math.floor(i * n / k)), folds = [];
    for (let i = 0; i < k; i++) {
      const [a, b] = [bounds[i], bounds[i + 1]], test = idx.slice(a, b), train = [...idx.slice(0, Math.max(0, a - purge)), ...idx.slice(Math.min(n, b + embargo))];
      let best = null; for (const pr of runs) { const o = objective(pick(pr[1].ret, train)); if (!best || o > best[0]) best = [o, pr]; }
      const [p, res] = best[1], m = metrics(pick(res.ret, test), pick(res.weight, test)), bm = metrics(pick(res.asset_ret, test));
      folds.push({ test: [f.t[test[0]], f.t[test[test.length - 1]]], chosen_params: p, test_metrics: m, benchmark: bm, beat_benchmark_sharpe: (m.sharpe ?? -9) > (bm.sharpe ?? -9) });
    }
    return { strategy: id, k, purge, embargo, folds, share_folds_beating_benchmark: mean(folds.map((x) => (x.beat_benchmark_sharpe ? 1 : 0))) };
  }

  // ---------- event studies, turning points, scoreboard ----------
  function signalLibrary(f, sf, th = 70) {
    const cross = (a, b, up) => a.map((x, i) => i > 0 && (up ? x > b[i] && a[i - 1] <= b[i - 1] : x < b[i] && a[i - 1] >= b[i - 1]));
    return {
      bottom_score: [`Bottom score >= ${th}`, ge(sf.bottom, th), "bullish"], top_score: [`Top risk score >= ${th}`, ge(sf.top, th), "bearish"],
      rsi_oversold: ["RSI(14) < 30", lt(f.rsi14, 30), "bullish"], rsi_overbought: ["RSI(14) > 75", gt(f.rsi14, 75), "bearish"],
      rsi_bull_div: ["Bullish RSI divergence", f.rsi_bull_div, "bullish"], rsi_bear_div: ["Bearish RSI divergence", f.rsi_bear_div, "bearish"],
      macd_bull_div: ["Bullish MACD divergence", f.macd_bull_div, "bullish"], macd_bear_div: ["Bearish MACD divergence", f.macd_bear_div, "bearish"],
      capitulation_volume: ["Capitulation volume (>=2x avg, >2% down)", and(ge(f.vol_ratio, 2), lt(f.ret1, -0.02)), "bullish"],
      extension: ["Extension: distance from 200d in top 5% of history", ge(P(f, "dist200"), 0.95), "bearish"],
      golden_cross: ["50-day crosses above 200-day", cross(f.sma50, f.sma200, true), "bullish"], death_cross: ["50-day crosses below 200-day", cross(f.sma50, f.sma200, false), "bearish"],
    };
  }
  function eventDates(sig, cooldown) { const out = []; let last = -1e9; for (let i = 0; i < sig.length; i++) if (sig[i] && !(i > 0 && sig[i - 1]) && i - last >= cooldown) { out.push(i); last = i; } return out; }
  function entryExit(b, h, a = 0, z = b.c.length) {
    const n = z - a, ret = nan(n), mae = nan(n);
    for (let t = 0; t + h < n; t++) {
      const e = ok(b.o[a + t + 1]) ? b.o[a + t + 1] : b.c[a + t + 1];
      ret[t] = b.c[a + t + h] / e - 1;
      let m = Infinity; for (let j = t + 1; j <= t + h; j++) m = Math.min(m, ok(b.l[a + j]) ? b.l[a + j] : b.c[a + j]);
      mae[t] = m / e - 1;
    }
    return { ret, mae };
  }
  function eventStudy(f, sig, direction, cooldown = 20, horizons = HORIZONS, nRandom = 2000, warmup = 252, seed = 3, a = 0, z = f.n) {
    const s = sig.slice(a, z).map((x) => x === true), n = z - a, ev = eventDates(s, cooldown).filter((i) => i >= warmup), r = rng(seed);
    const out = { n_events: ev.length, events: ev.map((i) => f.t[a + i]), horizons: {} };
    for (const h of horizons) {
      const { ret, mae } = entryExit(f.bars, h, a, z), base = ret.slice(warmup, Math.max(warmup, n - h)), evh = ev.filter((i) => i < n - h);
      const st = evh.length ? describe(evh.map((i) => ret[i])) : { n: 0 };
      st.baseline = describe(base);
      if (evh.length >= 5) {
        const obs = mean(evh.map((i) => ret[i])), sims = [];
        for (let k = 0; k < nRandom; k++) { let acc = 0, c = 0; for (let j = 0; j < evh.length; j++) { const v = base[randInt(r, base.length)]; if (ok(v)) { acc += v; c++; } } sims.push(acc / c); }
        st.p_value_vs_random = direction === "bullish" ? sims.filter((x) => x >= obs).length / nRandom : sims.filter((x) => x <= obs).length / nRandom;
        st.excess_mean = obs - mean(base); st.excess_median = median(evh.map((i) => ret[i])) - median(base); st.mae_median = median(evh.map((i) => mae[i]));
      }
      st.significant_5pct = (st.p_value_vs_random ?? 1) < 0.05;
      out.horizons[String(h)] = st;
    }
    return out;
  }
  function zigzag(c, pct = 0.1) {
    const piv = []; let trend = 0, hi = 0, lo = 0;
    for (let i = 1; i < c.length; i++) {
      if (trend === 0) {
        if (c[i] > c[hi]) hi = i; if (c[i] < c[lo]) lo = i;
        if (c[i] >= c[lo] * (1 + pct)) { piv.push([lo, "trough"]); trend = 1; hi = i; }
        else if (c[i] <= c[hi] * (1 - pct)) { piv.push([hi, "peak"]); trend = -1; lo = i; }
      } else if (trend === 1) { if (c[i] > c[hi]) hi = i; else if (c[i] <= c[hi] * (1 - pct)) { piv.push([hi, "peak"]); trend = -1; lo = i; } }
      else { if (c[i] < c[lo]) lo = i; else if (c[i] >= c[lo] * (1 + pct)) { piv.push([lo, "trough"]); trend = 1; hi = i; } }
    }
    return piv;
  }
  function turningPoints(f, sig, direction, pct = 0.1, window = 20, cooldown = 20) {
    const kind = direction === "bullish" ? "trough" : "peak", pp = zigzag(f.close, pct).filter((p) => p[1] === kind).map((p) => p[0]), ev = eventDates(sig.map((x) => x === true), cooldown);
    if (!ev.length || !pp.length) return { pivots: pp.length, signals: ev.length, precision: null, recall: null, pivot_threshold: pct, window };
    const hitSig = ev.map((e) => pp.some((p) => Math.abs(e - p) <= window)), hitPiv = pp.map((p) => ev.some((e) => Math.abs(e - p) <= window));
    const lags = ev.flatMap((e) => { const d = pp.map((p) => e - p).filter((x) => Math.abs(x) <= window); if (!d.length) return []; let best = d[0]; for (const x of d) if (Math.abs(x) < Math.abs(best)) best = x; return [best]; });
    return { pivot_threshold: pct, window, pivots: pp.length, signals: ev.length, precision: mean(hitSig.map(Number)), recall: mean(hitPiv.map(Number)),
      false_positives: hitSig.filter((x) => !x).length, missed_pivots: hitPiv.filter((x) => !x).length, median_lag_sessions: lags.length ? median(lags) : null,
      missed: pp.filter((_, k) => !hitPiv[k]).map((p) => f.t[p]).slice(0, 30) };
  }
  function signalStability(f, sig, direction, k = 5, horizon = 60, cooldown = 20) {
    const bounds = Array.from({ length: k + 1 }, (_, i) => Math.floor(252 + i * (f.n - 252) / k)), out = [];
    for (let i = 0; i < k; i++) {
      const [a, b] = [bounds[i], bounds[i + 1]], es = eventStudy(f, sig, direction, cooldown, [horizon], 500, 252, 3, Math.max(0, a - 252), b), h = es.horizons[String(horizon)];
      out.push({ period: [f.t[a], f.t[b - 1]], n_events: h.n || 0, excess_mean: h.excess_mean ?? null, hit_rate: h.hit_rate ?? null, baseline_hit_rate: h.baseline.hit_rate ?? null });
    }
    return out;
  }
  function bhQvalues(p) {
    const idx = p.flatMap((v, i) => (v === null || v === undefined ? [] : [i])); if (!idx.length) return p.map(() => null);
    const vals = idx.map((i) => p[i]), order = vals.map((_, i) => i).sort((a, b) => vals[a] - vals[b]), m = vals.length;
    const adj = order.map((j, r) => vals[j] * m / (r + 1));
    for (let r = m - 2; r >= 0; r--) adj[r] = Math.min(adj[r], adj[r + 1]);
    const out = p.map(() => null); order.forEach((j, r) => { out[idx[j]] = Math.min(1, Math.max(0, adj[r])); });
    return out;
  }
  function signalScoreboard(f, sf, horizon = 60) {
    const rows = Object.entries(signalLibrary(f, sf)).map(([name, [label, sig, direction]]) => {
      const h = eventStudy(f, sig, direction, 20, [horizon], 1000).horizons[String(horizon)];
      return { signal: name, label, direction, n_events: h.n || 0, median: h.median ?? null, hit_rate: h.hit_rate ?? null, baseline_hit_rate: h.baseline.hit_rate ?? null,
        excess_mean: h.excess_mean ?? null, p_value: h.p_value_vs_random ?? null, significant: h.significant_5pct, active_now: recent(sig.map((x) => x === true), 5)[f.n - 1] };
    });
    bhQvalues(rows.map((r) => r.p_value)).forEach((q, i) => { rows[i].q_value = q; rows[i].significant_after_fdr = q !== null && q < 0.1; });
    return rows.sort((a, b) => (a.p_value === null) - (b.p_value === null) || (a.p_value ?? 1) - (b.p_value ?? 1));
  }
  function signalReport(f, sf, name) {
    const [label, sig, direction] = signalLibrary(f, sf)[name];
    return { signal: name, label, direction, event_study: eventStudy(f, sig, direction), turning_points: turningPoints(f, sig, direction), stability_by_era: signalStability(f, sig, direction) };
  }

  // ---------- chart markers (server services.signals) ----------
  function declusteredCrosses(x, th, cooldown) { const out = []; let last = -1e9; for (let i = 1; i < x.length; i++) if (x[i] >= th && x[i - 1] < th && i - last > cooldown) { out.push(i); last = i; } return out; }
  function signalEvents(f, sf, bottomTh = 70, topTh = 70) {
    const fwd = (i) => Object.fromEntries([20, 60, 126].map((h) => [String(h), i + h < f.n ? f.close[i + h] / f.close[i] - 1 : null])), events = [];
    for (const [side, th] of [["bottom", bottomTh], ["top", topTh]]) for (const i of declusteredCrosses(sf[side], th, 20)) {
      const ex = explain(sf, i, side);
      events.push({ i, date: f.t[i], type: side, label: `${side === "bottom" ? "Bottom score" : "Top risk"} ${ex.score}`, score: ex.score, regime: ex.regime, triggered_by: ex.components.filter((c) => c.available).slice(0, 6), forward_realized: fwd(i) });
    }
    for (const e of f.rsi_div_events) { const i = f.t.indexOf(e.confirmed); events.push({ i, date: e.confirmed, type: `${e.type}_divergence`, label: `RSI ${e.type} divergence`, triggered_by: [{ label: "Price pivots", value: e.price }, { label: "RSI at pivots", value: e.osc }], forward_realized: fwd(i) }); }
    for (let i = 0; i < f.n; i++) {
      const r = sf.regime[i];
      if ((r === "bear_capitulation" || r === "distribution") && r !== (i ? sf.regime[i - 1] : undefined))
        events.push({ i, date: f.t[i], type: `regime_${r}`, label: REGIMES[r], triggered_by: regimeReasons(f, i).map((x) => ({ label: x })), forward_realized: fwd(i) });
    }
    return events.sort((a, b) => (a.date < b.date ? -1 : 1));
  }

  // ---------- stock state (server classify_row; relative strength not available here) ----------
  const STATE_LABELS = { potential_exhaustion: "Potential exhaustion", structural_decline: "Deeper structural decline", possible_accumulation: "Possible accumulation",
    normal_pullback: "Normal pullback", uptrend_intact: "Uptrend intact", neutral: "Neutral / no clear setup" };
  function stateInputs(f) {
    return { dist_pct: P(f, "dist200"), bear_div: recent(or(f.rsi_bear_div, f.macd_bear_div), 15), bull_div: recent(or(f.rsi_bull_div, f.macd_bull_div), 15), rs_p: f.rs126 ? P(f, "rs126") : nan(f.n) };
  }
  function classifyRow(f, x, i) {
    const g = (k) => f[k][i], [close, sma20, sma50, sma200, dd, r, slope] = ["close", "sma20", "sma50", "sma200", "drawdown", "rsi14", "sma200_slope"].map(g);
    const pc = (v) => `${Math.round(v * 100)}%`, sgn = (v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
    if ([close, sma50, sma200, dd, r, slope].some((v) => !ok(v))) return ["neutral", ["Insufficient history"]];
    const slopeUp = slope > 0, obvUp = (ok(g("obv_slope20")) ? g("obv_slope20") : 0) > 0.05, bearDiv = x.bear_div[i], bullDiv = x.bull_div[i], distP = x.dist_pct[i], rsP = x.rs_p[i];
    if (ok(distP) && distP >= 0.95 && (r >= 75 || bearDiv)) return ["potential_exhaustion", [`Distance from 200d (${sgn(g("dist200"))}) is in the top 5% of its own history`, `RSI ${r.toFixed(0)}` + (bearDiv ? ", bearish divergence" : "")]];
    if (close < sma200 && !slopeUp && dd <= -0.15 && (sma50 < sma200 || dd <= -0.25)) {
      const why = ["Below a falling 200-day average", sma50 < sma200 ? "50-day below 200-day" : "Drawdown beyond 25%", `Drawdown ${pc(dd)}`];
      if (ok(rsP) && rsP < 0.25) why.push("Relative strength vs benchmark in bottom quartile of its history");
      if (bullDiv || (obvUp && close > sma20)) { why.push("But: early accumulation evidence (bullish divergence / rising OBV)"); return ["possible_accumulation", why]; }
      return ["structural_decline", why];
    }
    if ((dd <= -0.15 || close < sma200) && (bullDiv || (obvUp && close > sma20 && close > sma50)))
      return ["possible_accumulation", [`Drawdown ${pc(dd)}`, close > sma50 ? "Price reclaimed short-term averages" : "Bullish divergence", obvUp ? "On-balance volume rising" : "Momentum improving"]];
    if (slopeUp && dd >= -0.15 && dd <= -0.04 && close > sma200 * 0.97) {
      const why = ["200-day average still rising", `Pullback of ${pc(dd)} from 52-week high`, `RSI ${r.toFixed(0)}`];
      if (ok(g("vol_ratio")) && g("vol_ratio") < 1.5) why.push("Volume not abnormal (no distribution spike)");
      return ["normal_pullback", why];
    }
    if (slopeUp && close > sma50) return ["uptrend_intact", ["Above rising 200-day and 50-day averages", `Drawdown ${pc(dd)}`]];
    return ["neutral", [`${close > sma200 ? "Above" : "Below"} the 200-day average (${sgn(g("dist200"))})`, `Drawdown ${pc(dd)}`, "No clear pullback, accumulation, decline, or exhaustion pattern"]];
  }
  function stateStats(f) { // forward returns after past dates in today's state (server analyze())
    const x = stateInputs(f), hist = []; for (let i = 260; i < f.n; i++) hist.push(classifyRow(f, x, i)[0]);
    const now = classifyRow(f, x, f.n - 1), stats = {};
    for (const h of [20, 60, 126]) {
      const fr = fwdReturn(f.close, h), vals = hist.flatMap((s, k) => (s === now[0] && ok(fr[260 + k]) ? [fr[260 + k]] : [])), all = fr.slice(260).filter(ok);
      stats[String(h)] = { n_samples: vals.length, median: vals.length ? median(vals) : null, hit_rate: vals.length ? vals.filter((v) => v > 0).length / vals.length : null, unconditional_median: median(all), unconditional_hit_rate: all.filter((v) => v > 0).length / all.length };
    }
    return { state: now[0], label: STATE_LABELS[now[0]], reasons: now[1], stats };
  }

  // ---------- scanner flags (server scanner.FLAGS) ----------
  const prevAll = (cond, n) => { const s = shift(cond.map(Number), 1); return rollMin(s, n).map((v) => v === 1); };
  const FLAGS = {
    oversold_confirmed: ["Extreme oversold with bullish confirmation", "bullish", (f) => and(recent(lt(f.rsi14, 30), 10), gt(f.rsi14, shift(f.rsi14)), gt(f.close, shift(f.close)))],
    bullish_divergence: ["Bullish RSI/MACD divergence", "bullish", (f) => recent(or(f.rsi_bull_div, f.macd_bull_div), 5)],
    capitulation_volume: ["Capitulation volume", "bullish", (f) => and(recent(and(ge(f.vol_ratio, 2.5), lt(f.ret1, -0.03)), 5), f.vol_ratio.map(ok))],
    support_test: ["Test of rising 200-day support", "bullish", (f) => and(gt(f.close, f.sma200), lt(sub(f.close, f.sma200), f.atr14), gt(f.sma200_slope, 0))],
    trend_reversal_up: ["Trend reversal up (reclaimed 50-day)", "bullish", (f) => recent(and(gt(f.close, f.sma50), prevAll(lt(f.close, f.sma50), 20)), 5)],
    bearish_divergence_high: ["Bearish divergence near highs", "bearish", (f) => and(recent(or(f.rsi_bear_div, f.macd_bear_div), 5), gt(f.drawdown, -0.05))],
    unsustainable_extension: ["Unsustainable extension above 200-day", "bearish", (f) => ge(P(f, "dist200"), 0.97)],
    overbought_climax: ["Overbought on climax volume", "bearish", (f) => recent(and(gt(f.rsi14, 80), gt(f.vol_ratio, 2)), 5)],
    trend_reversal_down: ["Trend reversal down (lost 50-day)", "bearish", (f) => recent(and(lt(f.close, f.sma50), prevAll(gt(f.close, f.sma50), 40)), 5)],
  };
  /** One symbol's scanner row and its flag-onset excess returns (pooled by the caller across symbols). */
  function scanSymbol(sym, f, H = 20) {
    const fwd = f.close.map((c, i) => (i + H < f.n ? f.close[i + H] / c - 1 : NaN)), base = mean(fwd), active = [], excess = {};
    for (const [id, [, , fn]] of Object.entries(FLAGS)) {
      const sig = fn(f).map((x) => x === true);
      excess[id] = sig.flatMap((s, i) => (s && !(i > 0 && sig[i - 1]) && ok(fwd[i]) ? [fwd[i] - base] : []));
      if (sig[f.n - 1]) active.push(id);
    }
    const L = f.n - 1;
    return { row: { symbol: sym, as_of: f.t[L], close: f.close[L], rsi14: f.rsi14[L], dist200: f.dist200[L], drawdown: f.drawdown[L], rs63: f.rs63 ? f.rs63[L] : NaN, vol_ratio: f.vol_ratio[L], flags: active }, excess };
  }
  function scanSummary(results) {
    const pooled = Object.fromEntries(Object.keys(FLAGS).map((k) => [k, results.flatMap((r) => r.excess[k])]));
    const stats = Object.fromEntries(Object.entries(pooled).map(([k, v]) => {
      const sd = std(v);
      return [k, { label: FLAGS[k][0], direction: FLAGS[k][1], n: v.length, excess_mean: v.length ? mean(v) : null, excess_hit_rate: v.length ? v.filter((x) => x > 0).length / v.length : null, t_stat: v.length > 10 && sd > 0 ? mean(v) / (sd / Math.sqrt(v.length)) : null }];
    }));
    const rows = results.map(({ row }) => {
      for (const d of ["bullish", "bearish"]) {
        const act = row.flags.filter((x) => FLAGS[x][1] === d), sign = d === "bullish" ? 1 : -1;
        row[d + "_confluence"] = act.length;
        row[d + "_evidence"] = Math.round(act.reduce((s, x) => s + (stats[x].n >= 30 ? Math.max(0, sign * (stats[x].excess_mean || 0)) : 0), 0) * 100 * 1000) / 1000;
      }
      return row;
    });
    return { rows, flag_evidence: stats };
  }

  root.SMBTQ = { technicalFrame, scoreFrame, explain, classifyRegime, regimeReasons, REGIMES, BOTTOM, TOP, scoreProbabilities, describe, HORIZONS,
    findAnalogs, compareKeyDates, run, metrics, STRATEGIES, walkForward, purgedCv, signalLibrary, eventStudy, signalScoreboard, signalReport, turningPoints, bhQvalues,
    signalEvents, stateInputs, classifyRow, stateStats, STATE_LABELS, FLAGS, scanSymbol, scanSummary, expandingPct, fwdReturn, fwdMinReturn, zigzag, ok };
})(typeof window !== "undefined" ? window : globalThis);
