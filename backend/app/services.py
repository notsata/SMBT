"""Assembles DB data + quant modules into API-ready results, cached per data version."""

import math
import threading
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .data.catalog import CATALOG, CBOE_INDEXES, UNAVAILABLE
from .data.quality import is_stale
from .data.store import bar_meta, instruments, load_bars, load_close_matrix, membership_mask, pit_series, series_meta
from .db import IngestRun, Instrument, kv_get
from .quant import analogs, scores
from .quant.breadth import breadth_frame
from .quant.macro import fear_greed, macro_frame, macro_risk, sentiment_frame
from .quant.probability import HORIZONS, conditional, fwd_return, score_probabilities
from .quant.technical import anchored_vwap, divergences, expanding_pct, recent, technical_frame, volume_profile

_cache: dict[str, tuple[int, object]] = {}
_locks: dict[str, threading.Lock] = {}
_glock = threading.Lock()


class NotFound(Exception):
    pass


def data_version(s: Session) -> int:
    return int((kv_get(s, "data_version") or {}).get("v", 0))


def cached(s: Session, key: str, fn):
    v = data_version(s)
    hit = _cache.get(key)
    if hit and hit[0] == v:
        return hit[1]
    with _glock:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:  # one computation per key; concurrent callers wait for it
        hit = _cache.get(key)
        if hit and hit[0] == v:
            return hit[1]
        val = fn()
        _cache[key] = (v, val)
        return val


def clean(x):
    """JSON-safe: NaN/inf -> None, numpy/pandas scalars -> Python."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, pd.Timestamp):
        return x.date().isoformat()
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return x


def _num(v, nd=4):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def symbols_with_data(s: Session) -> dict[str, dict]:
    return cached(s, "bar_meta", lambda: bar_meta(s))


def default_benchmark(s: Session) -> str:
    meta = symbols_with_data(s)
    for sym in get_settings().benchmark_list + ["^GSPC"]:
        if sym in meta:
            return sym
    if not meta:
        raise NotFound("No price data loaded. Run ingestion (`python -m app.cli ingest`) or load sample data (`python -m app.cli sample`).")
    return sorted(meta)[0]


def bars(s: Session, symbol: str) -> pd.DataFrame:
    symbol = symbol.upper()

    def load():
        df = load_bars(s, symbol)
        if df.empty:
            raise NotFound(f"No data for {symbol}")
        return df

    return cached(s, f"bars:{symbol}", load)


def breadth(s: Session) -> tuple[pd.DataFrame | None, dict]:
    def compute():
        members = [i.symbol for i in instruments(s, in_breadth=True)]
        info = {"universe": "S&P 500 members", "members": len(members), "available": False}
        if len(members) < 100:
            info["note"] = "Breadth unavailable: constituent prices not ingested (needs network ingestion)."
            return None, info
        m = load_close_matrix(s, members)
        if m.shape[1] < 100:
            info["note"] = f"Breadth unavailable: only {m.shape[1]} members have price data."
            return None, info
        mask = membership_mask(s, "SP500", m.index, list(m.columns))
        bf = breadth_frame(m, mask)
        info |= {
            "available": True,
            "with_data": int(m.shape[1]),
            "survivorship_bias": mask is None,
            "note": "Point-in-time membership applied." if mask is not None
            else "History uses today's members (survivorship bias: past breadth looks healthier than it was). Set SP500_HISTORY_URL to correct.",
            "last_date": m.index[-1].date().isoformat(),
        }
        return bf, info

    return cached(s, "breadth", compute)


def _vol_index(s: Session, sym: str) -> pd.Series | None:
    try:
        return bars(s, sym)["close"]
    except NotFound:
        return None


def market_frame(s: Session, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Full feature frame + score frame for an index/ETF, with provenance for every block."""
    symbol = symbol.upper()

    def compute():
        b = bars(s, symbol)
        bench = default_benchmark(s)
        bench_close = bars(s, bench)["close"] if bench != symbol else None
        f = technical_frame(b, bench_close)
        meta = {"price": {"symbol": symbol, "source": b.attrs["source"], "last_date": b.attrs["last_date"], "fetched_at": b.attrs["fetched_at"], "kind": "end-of-day"}}
        bf, binfo = breadth(s)
        meta["breadth"] = binfo
        if bf is not None:
            f = f.join(bf.reindex(f.index))
        vix, vix3m = _vol_index(s, "^VIX"), _vol_index(s, "^VIX3M")
        pit_cache: dict = {}

        def pit(sid, transform=None):
            key = (sid, getattr(transform, "__name__", None))
            if key not in pit_cache:
                pit_cache[key] = pit_series(s, sid, f.index, transform)["value"]
            return pit_cache[key]

        f = f.join(sentiment_frame(f.index, vix, vix3m, pit)).join(macro_frame(f.index, pit))
        tlt = _vol_index(s, "TLT")
        if tlt is not None:
            f["stock_bond_20"] = f["roc20"] - tlt.reindex(f.index).pct_change(20, fill_method=None)
        f = f.join(fear_greed(f)).join(macro_risk(f))
        meta["vix"] = {"available": vix is not None, "term_structure": vix3m is not None}
        if vix is not None:
            vb = bars(s, "^VIX")
            meta["vix"] |= {"source": vb.attrs["source"], "last_date": vb.attrs["last_date"]}
        meta["series"] = {sid: m for sid in CATALOG if (m := series_meta(s, sid))}
        sf = scores.score_frame(f)
        return f, sf, meta

    return cached(s, f"frame:{symbol}", compute)


def _card(value, label, as_of, source, stale_ref=None, **extra):
    st = get_settings()
    stale = is_stale(date.fromisoformat(stale_ref), max_age_days=st.stale_after_days) if stale_ref else None
    return {"label": label, "value": value, "as_of": as_of, "source": source, "stale": stale, "available": value is not None, **extra}


def overview(s: Session, symbol: str | None = None) -> dict:
    symbol = (symbol or default_benchmark(s)).upper()

    def compute():
        f, sf, meta = market_frame(s, symbol)
        last = f.iloc[-1]
        srow = sf.iloc[-1]
        as_of = f.index[-1].date().isoformat()
        psrc = meta["price"]["source"]
        quote = kv_get(s, f"quote:{symbol}")
        cards = {
            "regime": _card(srow["regime"] if isinstance(srow["regime"], str) else None, "Market regime", as_of, "Derived: price trend, drawdown, VIX, breadth",
                            as_of, display=scores.REGIMES.get(srow["regime"], "Unavailable"), reasons=scores.regime_reasons(f)),
            "bottom": _card(_num(srow["bottom"], 1), "Bottom Opportunity Score", as_of, "Heuristic composite (see components)", as_of,
                            coverage=_num(srow["bottom_cov"], 2), kind="heuristic"),
            "top": _card(_num(srow["top"], 1), "Top Risk Score", as_of, "Heuristic composite (see components)", as_of, coverage=_num(srow["top_cov"], 2), kind="heuristic"),
            "trend": _card(_num(srow["trend"], 1), "Trend Confidence", as_of, "ADX, MA alignment, R-squared, persistence, breadth", as_of, direction=srow["trend_dir"], kind="heuristic"),
        }
        vm = meta["vix"]
        cards["vix"] = _card(_num(last.get("vix"), 2), "VIX", vm.get("last_date"), vm.get("source", "unavailable"), vm.get("last_date"),
                             percentile=_num(expanding_pct(f["vix"]).iloc[-1], 3) if "vix" in f else None,
                             term_ratio=_num(last.get("vix_term"), 3))
        bi = meta["breadth"]
        cards["breadth"] = _card(_num(last.get("pct_above_50"), 1), "% S&P 500 above 50-day", bi.get("last_date"), bi["universe"], bi.get("last_date"),
                                 note=bi.get("note"), pct_above_200=_num(last.get("pct_above_200"), 1), mcclellan=_num(last.get("mcclellan_osc"), 1),
                                 nhnl=_num(last.get("nhnl_pct"), 1))
        fgn = int(last.get("fear_greed_n") or 0)
        cards["sentiment"] = _card(_num(last.get("fear_greed"), 1), "Fear & Greed (reconstructed)", as_of,
                                   f"Reconstruction from {fgn} available inputs (not CNN's official index)", as_of,
                                   components={k[3:]: _num(last[k], 1) for k in f.columns if k.startswith("fg:")})
        cards["macro"] = _card(_num(last.get("macro_risk"), 1), "Macro Risk", as_of, "FRED series, point-in-time", None,
                               components={k[3:]: _num(last[k], 2) for k in f.columns if k.startswith("mr:")})
        return {
            "symbol": symbol,
            "as_of": as_of,
            "data_kind": "End-of-day bars. Scores update after each daily close; not real-time.",
            "price": {"close": _num(last["close"], 2), "change_1d": _num(last["ret1"], 4), "source": psrc, "quote": quote},
            "cards": cards,
            "explain": {"bottom": scores.explain(sf, f, side="bottom"), "top": scores.explain(sf, f, side="top"),
                        "trend": {k[3:]: _num(srow[k], 3) for k in sf.columns if k.startswith("tc:")}},
            "probabilities": score_probabilities(sf, f["close"]),
            "meta": meta,
            "unavailable": UNAVAILABLE,
        }

    return clean(cached(s, f"overview:{symbol}", compute))


def chart(s: Session, symbol: str, overlays: bool = True) -> dict:
    symbol = symbol.upper()

    def compute():
        b = bars(s, symbol)
        f = technical_frame(b)
        out = {
            "symbol": symbol,
            "source": b.attrs["source"],
            "last_date": b.attrs["last_date"],
            "bars": [
                {"t": d.date().isoformat(), "o": _num(r.open, 4), "h": _num(r.high, 4), "l": _num(r.low, 4), "c": _num(r.close, 4), "v": _num(r.volume, 0)}
                for d, r in b.iterrows()
            ],
        }
        if overlays:
            out["overlays"] = {k: [_num(v, 4) for v in f[k]] for k in ("sma20", "sma50", "sma200", "bb_upper", "bb_lower")}
            lo_anchor = b["close"].tail(252).idxmin()
            out["overlays"]["avwap_from_252d_low"] = [_num(v, 4) for v in anchored_vwap(b, lo_anchor)]
            out["avwap_anchor"] = lo_anchor.date().isoformat()
            out["volume_profile"] = volume_profile(b)
        return out

    return clean(cached(s, f"chart:{symbol}", compute))


INDICATOR_COLUMNS = {
    "rsi14": "RSI(14)", "macd": "MACD", "macd_hist": "MACD histogram", "stochrsi_k": "Stoch RSI %K", "bb_pctb": "Bollinger %B",
    "adx": "ADX", "atr_pct": "ATR %", "hv20": "Hist. vol (20d)", "dist200": "Distance from 200d", "drawdown": "Drawdown",
    "rs63": "Relative strength vs benchmark (63d)", "obv": "On-balance volume", "pct_above_50": "% above 50d", "pct_above_200": "% above 200d",
    "mcclellan_osc": "McClellan Oscillator", "mcclellan_sum": "McClellan Summation", "nhnl_pct": "Net new highs %", "pct_oversold": "% oversold",
    "ad_line": "Advance/decline line", "vix": "VIX", "vix_term": "VIX / VIX3M", "pcr10": "Put/call (10d)", "fear_greed": "Fear & Greed (recon.)",
    "curve_10y2y": "10Y-2Y curve", "credit_spread": "Credit spread", "real_10y": "10Y real yield", "fed_funds": "Fed funds",
    "nfci": "NFCI", "sahm": "Sahm gap", "core_cpi_yoy": "Core CPI YoY", "net_liquidity": "Net liquidity ($bn)", "macro_risk": "Macro risk",
    "bottom": "Bottom Opportunity Score", "top": "Top Risk Score", "trend": "Trend Confidence",
}


def indicator_series(s: Session, symbol: str, names: list[str]) -> dict:
    f, sf, _ = market_frame(s, symbol)
    both = f.join(sf[["bottom", "top", "trend"]])
    out = {"symbol": symbol.upper(), "t": [d.date().isoformat() for d in both.index], "series": {}, "labels": {}}
    for n in names:
        if n in both and both[n].notna().any():
            out["series"][n] = [_num(v, 4) for v in both[n].astype(float)]
            out["labels"][n] = INDICATOR_COLUMNS.get(n, n)
    out["available"] = sorted(k for k in INDICATOR_COLUMNS if k in both and both[k].notna().any())
    return clean(out)


def _declustered_crosses(x: pd.Series, th: float, cooldown: int) -> list[pd.Timestamp]:
    cross = (x >= th) & (x.shift() < th)
    out, last = [], -10**9
    for i in np.flatnonzero(cross.fillna(False).values):
        if i - last > cooldown:
            out.append(x.index[i])
            last = i
    return out


def signals(s: Session, symbol: str, bottom_th: float = 70, top_th: float = 70) -> dict:
    symbol = symbol.upper()

    def compute():
        f, sf, _ = market_frame(s, symbol)
        close = f["close"]
        events = []

        def fwd(d):
            i = f.index.get_loc(d)
            return {str(h): _num(close.iloc[i + h] / close.iloc[i] - 1) if i + h < len(close) else None for h in (20, 60, 126)}

        for d in _declustered_crosses(sf["bottom"], bottom_th, 20):
            ex = scores.explain(sf, f, d, "bottom")
            events.append({"date": d.date().isoformat(), "type": "bottom", "label": f"Bottom score {ex['score']}", "score": ex["score"],
                           "regime": ex["regime"], "triggered_by": [c for c in ex["components"] if c["available"]][:6], "forward_realized": fwd(d)})
        for d in _declustered_crosses(sf["top"], top_th, 20):
            ex = scores.explain(sf, f, d, "top")
            events.append({"date": d.date().isoformat(), "type": "top", "label": f"Top risk {ex['score']}", "score": ex["score"],
                           "regime": ex["regime"], "triggered_by": [c for c in ex["components"] if c["available"]][:6], "forward_realized": fwd(d)})
        _, _, div = divergences(close, f["rsi14"])
        for e in div:
            d = pd.Timestamp(e["confirmed"])
            events.append({"date": e["confirmed"], "type": f"{e['type']}_divergence", "label": f"RSI {e['type']} divergence",
                           "triggered_by": [{"label": "Price pivots", "value": e["price"]}, {"label": "RSI at pivots", "value": e["osc"]},
                                            {"label": "Pivot dates", "value": [e["pivot1"], e["pivot2"]]}], "forward_realized": fwd(d)})
        if "zweig_thrust" in f:
            for d in f.index[f["zweig_thrust"].fillna(False).astype(bool)]:
                events.append({"date": d.date().isoformat(), "type": "zweig_thrust", "label": "Zweig breadth thrust",
                               "triggered_by": [{"label": "10d EMA of A/(A+D)", "value": _num(f.at[d, "zweig_ema"], 3)}], "forward_realized": fwd(d)})
        reg = sf["regime"]
        for d in reg.index[(reg != reg.shift()) & reg.isin(["bear_capitulation", "distribution"])]:
            events.append({"date": d.date().isoformat(), "type": f"regime_{reg[d]}", "label": scores.REGIMES[reg[d]],
                           "triggered_by": [{"label": r} for r in scores.regime_reasons(f, f.index.get_loc(d))], "forward_realized": fwd(d)})
        events.sort(key=lambda e: e["date"])
        return {"symbol": symbol, "thresholds": {"bottom": bottom_th, "top": top_th}, "events": events,
                "note": "Signals are generated with data available on each date. 'forward_realized' shows what happened afterwards (evaluation only)."}

    return clean(cached(s, f"signals:{symbol}:{bottom_th}:{top_th}", compute))


def _state_inputs(f: pd.DataFrame) -> pd.DataFrame:
    """Causal helper columns for the state classifier, computed once for the whole history."""
    return pd.DataFrame({
        "dist_pct": expanding_pct(f["dist200"]),
        "bear_div": recent(f["rsi_bear_div"] | f["macd_bear_div"], 15),
        "bull_div": recent(f["rsi_bull_div"] | f["macd_bull_div"], 15),
        "rs_p": expanding_pct(f["rs126"]) if "rs126" in f else np.nan,
    }, index=f.index)


def classify_row(r: dict, x: dict) -> tuple[str, list[str]]:
    """Normal pullback vs accumulation vs structural decline vs exhaustion, from one row of causal features."""
    g = lambda k: r.get(k) if r.get(k) is not None and not pd.isna(r.get(k)) else np.nan  # noqa: E731
    close, sma20, sma50, sma200, dd, rsi_, slope = (g(k) for k in ("close", "sma20", "sma50", "sma200", "drawdown", "rsi14", "sma200_slope"))
    if any(pd.isna(v) for v in (close, sma50, sma200, dd, rsi_, slope)):
        return "neutral", ["Insufficient history"]
    slope_up = slope > 0
    obv_up = (g("obv_slope20") if pd.notna(g("obv_slope20")) else 0) > 0.05
    bear_div, bull_div, dist_pct, rs_p = bool(x["bear_div"]), bool(x["bull_div"]), x["dist_pct"], x["rs_p"]
    if pd.notna(dist_pct) and dist_pct >= 0.95 and (rsi_ >= 75 or bear_div):
        return "potential_exhaustion", [f"Distance from 200d ({g('dist200'):+.1%}) is in the top 5% of its own history",
                                        f"RSI {rsi_:.0f}" + (", bearish divergence" if bear_div else "")]
    if close < sma200 and not slope_up and dd <= -0.15 and (sma50 < sma200 or dd <= -0.25):
        reasons = ["Below a falling 200-day average", "50-day below 200-day" if sma50 < sma200 else "Drawdown beyond 25%", f"Drawdown {dd:.0%}"]
        if pd.notna(rs_p) and rs_p < 0.25:
            reasons.append("Relative strength vs benchmark in bottom quartile of its history")
        if bull_div or (obv_up and close > sma20):
            reasons.append("But: early accumulation evidence (bullish divergence / rising OBV)")
            return "possible_accumulation", reasons
        return "structural_decline", reasons
    if (dd <= -0.15 or close < sma200) and (bull_div or (obv_up and close > sma20 and close > sma50)):
        return "possible_accumulation", [f"Drawdown {dd:.0%}", "Price reclaimed short-term averages" if close > sma50 else "Bullish divergence",
                                         "On-balance volume rising" if obv_up else "Momentum improving"]
    if slope_up and -0.15 <= dd <= -0.04 and close > sma200 * 0.97:
        reasons = ["200-day average still rising", f"Pullback of {dd:.0%} from 52-week high", f"RSI {rsi_:.0f}"]
        if pd.notna(g("vol_ratio")) and g("vol_ratio") < 1.5:
            reasons.append("Volume not abnormal (no distribution spike)")
        return "normal_pullback", reasons
    if slope_up and close > sma50:
        return "uptrend_intact", ["Above rising 200-day and 50-day averages", f"Drawdown {dd:.0%}"]
    return "neutral", [f"{'Above' if close > sma200 else 'Below'} the 200-day average ({g('dist200'):+.1%})", f"Drawdown {dd:.0%}",
                       "No clear pullback, accumulation, decline, or exhaustion pattern"]


def classify_stock(f: pd.DataFrame) -> tuple[str, list[str]]:
    return classify_row(f.iloc[-1].to_dict(), _state_inputs(f).iloc[-1].to_dict())


STATE_LABELS = {
    "potential_exhaustion": "Potential exhaustion",
    "structural_decline": "Deeper structural decline",
    "possible_accumulation": "Possible accumulation",
    "normal_pullback": "Normal pullback",
    "uptrend_intact": "Uptrend intact",
    "neutral": "Neutral / no clear setup",
}


def state_history(f: pd.DataFrame, min_len: int = 260) -> pd.Series:
    """Classification at every date using only data up to that date (inputs are causal)."""
    x = _state_inputs(f).to_dict("records")
    rows = f.to_dict("records")
    return pd.Series([classify_row(rows[i], x[i])[0] for i in range(min_len, len(f))], index=f.index[min_len:])


def analyze(s: Session, symbol: str) -> dict:
    symbol = symbol.upper()

    def compute():
        b = bars(s, symbol)
        bench = default_benchmark(s)
        bench_close = bars(s, bench)["close"]
        f = technical_frame(b, bench_close)
        if len(f) < 260:
            raise NotFound(f"{symbol}: need at least 260 sessions of history (have {len(f)})")
        inst = s.get(Instrument, symbol)
        state, reasons = classify_stock(f)
        r = f.iloc[-1]
        hist = state_history(f)
        state_stats = {}
        for h in (20, 60, 126):
            fr = fwd_return(f["close"], h).reindex(hist.index)
            vals = fr[hist == state].dropna().values
            state_stats[str(h)] = {"n_samples": int(len(vals)), "median": _num(np.median(vals)) if len(vals) else None,
                                   "hit_rate": _num((vals > 0).mean()) if len(vals) else None,
                                   "unconditional_median": _num(fr.dropna().median()), "unconditional_hit_rate": _num((fr.dropna() > 0).mean())}
        sector = None
        if inst and inst.sector_etf:
            try:
                sb = bars(s, inst.sector_etf)["close"]
                sector = {"etf": inst.sector_etf, "sector": inst.sector,
                          "sector_rs63_vs_benchmark": _num((sb / bench_close.reindex(sb.index)).pct_change(63).iloc[-1]),
                          "stock_rs63_vs_sector": _num((b["close"] / sb.reindex(b.index)).pct_change(63).iloc[-1])}
            except NotFound:
                sector = {"etf": inst.sector_etf, "note": "sector ETF data not loaded"}
        market = None
        try:
            ov = overview(s, bench)
            market = {"benchmark": bench, "regime": ov["cards"]["regime"]["display"], "bottom": ov["cards"]["bottom"]["value"], "top": ov["cards"]["top"]["value"]}
        except NotFound:
            pass
        _, _, div = divergences(f["close"], f["rsi14"])
        return {
            "symbol": symbol,
            "name": inst.name if inst else symbol,
            "as_of": f.index[-1].date().isoformat(),
            "source": b.attrs["source"],
            "state": state,
            "state_label": STATE_LABELS[state],
            "reasons": reasons,
            "state_forward_stats": state_stats,
            "state_stats_note": "Forward returns after past dates in the same state on this symbol. Consecutive days overlap, so n_samples overstates independent evidence.",
            "indicators": {k: _num(r.get(k)) for k in ("close", "sma20", "sma50", "sma200", "dist200", "rsi14", "macd_hist", "stochrsi_k", "bb_pctb",
                                                        "adx", "atr_pct", "hv20", "drawdown", "roc20", "roc63", "vol_ratio", "rs63", "rs126", "r2_63")},
            "percentiles": {k: _num(expanding_pct(f[k]).iloc[-1], 3) for k in ("rsi14", "dist200", "drawdown", "hv20", "rs126") if k in f},
            "recent_divergences": div[-5:],
            "sector": sector,
            "market": market,
            "valuation": {"available": False, "note": UNAVAILABLE["relative_valuation"]},
            "benchmark": bench,
        }

    return clean(cached(s, f"analyze:{symbol}", compute))


def status(s: Session) -> dict:
    st = get_settings()
    meta = symbols_with_data(s)
    runs = s.execute(select(IngestRun).order_by(IngestRun.id.desc()).limit(300)).scalars().all()
    last_by = {}
    for r in runs:
        last_by.setdefault((r.source, r.target), r)
    errors = [{"source": r.source, "target": r.target, "at": r.started_at.isoformat(), "message": r.message} for r in last_by.values() if r.status == "error"][:30]
    prices = []
    for sym in st.benchmark_list + list(CBOE_INDEXES) + ["^GSPC", "^IXIC", "TLT"]:
        m = meta.get(sym)
        if m:
            prices.append({"symbol": sym, **m, "stale": is_stale(date.fromisoformat(m["last"]), max_age_days=st.stale_after_days)})
    series = []
    for sid, d in CATALOG.items():
        m = series_meta(s, sid)
        series.append({"id": sid, "name": d.name, "category": d.category, "freq": d.freq, "default_source": d.source, "note": d.note,
                       "available": m is not None, **(m or {})})
    bf, binfo = breadth(s)
    return clean({
        "symbols_loaded": len(meta),
        "prices": prices,
        "series": series,
        "breadth": binfo,
        "recent_errors": errors,
        "auth_enabled": bool(st.app_access_token),
        "providers": {"tiingo": bool(st.tiingo_api_key), "fred_api": bool(st.fred_api_key), "analyst": bool(st.anthropic_api_key),
                      "push": bool(st.vapid_public_key and st.vapid_private_key)},
        "unavailable": UNAVAILABLE,
        "data_version": data_version(s),
        "now": datetime.now(timezone.utc).isoformat(),
    })


def outcome_after_condition(f: pd.DataFrame, cond: pd.Series, horizon: int, threshold: float) -> dict:
    """How often a >= threshold drop followed days matching `cond` (used by the AI analyst)."""
    from .quant.probability import describe, episodes, fwd_min_return

    lab = (fwd_min_return(f["close"], horizon) <= -threshold).astype(float).where(fwd_min_return(f["close"], horizon).notna())
    m = cond.fillna(False).astype(bool) & lab.notna()
    ep = episodes(m, horizon)
    return {"condition_days": int(m.sum()), "episodes": int(len(np.unique(ep))) if len(ep) else 0,
            "frequency": _num(lab[m].mean()) if m.any() else None, "base_rate": _num(lab.mean()),
            "detail": describe(lab[m].values, ep) if m.any() else None}
