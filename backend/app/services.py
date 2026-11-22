"""Assembles live provider data + quant modules into API results. Nothing is persisted: history comes from the
providers on demand and lives in process memory; live values come from the streaming hub.

Two bases are kept apart everywhere:
- confirmed: computed from completed daily sessions only (used for validation, probabilities, backtests);
- provisional: the same computation with today's in-progress bar from the live feed appended (display only).
"""

import math
import threading
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from .config import get_settings
from .market import calendar as cal
from .market import history, licensing, reference
from .market.analytics import breadth, options_summary
from .market.cache import TTLCache
from .market.hub import hub
from .market.macro import CATALOG, macro
from .market.model import Status, Unavailable, iso
from .quant import scores
from .quant.macro import fear_greed, macro_frame, macro_risk, sentiment_frame
from .quant.probability import fwd_return, score_probabilities
from .quant.technical import anchored_vwap, divergences, expanding_pct, recent, technical_frame, volume_profile

_frames = TTLCache(maxsize=96)
live_scores: dict[str, dict] = {}  # symbol -> provisional score snapshot (written by the refresh loop)
_live_lock = threading.Lock()

LIMITATIONS = {
    "nyse_advance_decline": "Exchange-wide NYSE advance/decline data has no free official source; breadth uses S&P 500 members.",
    "cboe_put_call": "Cboe's market-wide put/call ratios are published only as web pages under Cboe's terms (no API); the free history ends in 2019. "
                     "With a Massive Options plan the app shows SPY/QQQ options put/call ratios instead (no history, so not used in scores).",
    "aaii_sentiment": "AAII survey data requires AAII membership and has no public API.",
    "naaim_exposure": "NAAIM publishes a spreadsheet, not an API; not integrated.",
    "short_interest": "Exchange short interest (FINRA) is bi-monthly and not integrated.",
    "cape": "Shiller CAPE has no API; market cap / GDP (FRED, Federal Reserve Z.1) is used for valuation.",
    "unusual_options": "Unusual options activity needs a licensed real-time options trade feed.",
    "institutional_positioning": "13F institutional ownership needs a paid fundamentals plan; not integrated.",
    "earnings_revisions_history": "Point-in-time estimate revision history needs a licensed consensus dataset (e.g. LSEG I/B/E/S).",
}


def clean(x):
    """JSON-safe: NaN/inf -> None, numpy/pandas scalars -> Python."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
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


def default_benchmark() -> str:
    return get_settings().benchmark_list[0]


def bars(symbol: str) -> pd.DataFrame:
    df = history.daily(symbol.upper())
    licensing.check([df.attrs["provider"]], f"{symbol.upper()} price history")
    return df


def _maybe(symbol: str) -> pd.DataFrame | None:
    try:
        return bars(symbol)
    except Unavailable:
        return None


def provisional_bar(symbol: str) -> dict | None:
    """Today's in-progress regular-session bar from the live feed, only while that feed is LIVE or DELAYED."""
    st = hub.states.get(symbol.upper())
    if st is None or cal.session_at() not in (cal.Session.REGULAR, cal.Session.POST):
        return None
    status, _ = hub.status(st)
    if status not in (Status.LIVE, Status.DELAYED) or st.session_date is None:
        return None
    close = st.reg_last if not st.is_index else st.last
    if close is None or st.day_open is None:
        return None
    return {"date": pd.Timestamp(st.session_date), "open": st.day_open, "high": st.day_high, "low": st.day_low, "close": close,
            "volume": st.day_volume or np.nan, "provider": st.provider, "feed": st.feed, "status": str(status), "as_of": iso(st.last_ts)}


def _append(df: pd.DataFrame, pb: dict | None) -> pd.DataFrame:
    if pb is None or pb["date"] <= df.index[-1]:
        return df
    row = pd.DataFrame([{k: pb[k] for k in ("open", "high", "low", "close", "volume")}], index=pd.DatetimeIndex([pb["date"]], name="date"))
    out = pd.concat([df, row.astype(float)])
    out.attrs = dict(df.attrs)
    return out


def _versions(symbol: str) -> str:
    st = get_settings()
    lic = st.access_mode + "+" + ",".join(sorted(st.licensed))  # licence settings change which inputs a frame may use
    return f"{history.version(symbol)}:{history.version(default_benchmark())}:{history.version('^VIX')}:{macro.version}:{breadth.version}:{lic}"


def market_frame(symbol: str, provisional: bool = False):
    """(features, scores, meta). provisional=True appends today's live bar; returns None when no live bar exists."""
    symbol = symbol.upper()
    if not provisional:
        res = _frames.get_or_compute(f"frame:{symbol}:{_versions(symbol)}", lambda: (_compute_frame(symbol, False), 3600))
        licensing.check([res[2]["price"]["provider"]], f"{symbol} price history")  # enforced per request, not only on compute
        return res
    return _compute_frame(symbol, True)


def _compute_frame(symbol: str, provisional: bool):
    b = bars(symbol)
    sources = {b.attrs["provider"]}
    pb = provisional_bar(symbol) if provisional else None
    if provisional:
        if pb is None:
            return None
        licensing.check([pb["provider"]], f"{symbol} live price")
        sources.add(pb["provider"])
        b = _append(b, pb)
    bench = default_benchmark()
    bench_close = None
    if bench != symbol and (bb := _maybe(bench)) is not None:
        bench_close = _append(bb, provisional_bar(bench) if provisional else None)["close"]
        sources.add(bb.attrs["provider"])
    f = technical_frame(b, bench_close)
    meta = {"price": {k: b.attrs.get(k) for k in ("provider", "authority", "adjustment", "first", "last", "fetched_at", "issues",
                                                  "complete_through_last_session", "stale")} | {"symbol": symbol},
            "provisional": pb and {k: pb[k] for k in ("provider", "feed", "status", "as_of")}}
    # breadth
    if breadth.frame is not None and not licensing.blocked(breadth.sources()):
        bf = breadth.frame
        if provisional and breadth.provisional:
            bf = pd.concat([bf, pd.DataFrame([breadth.provisional["row"]], index=[f.index[-1]])])
        f = f.join(bf.reindex(f.index))
        sources |= breadth.sources()
        meta["breadth"] = breadth.info | {"provisional_as_of": breadth.provisional["as_of"] if provisional and breadth.provisional else None}
    else:
        meta["breadth"] = breadth.info if breadth.frame is None else {"available": False, "note": "Breadth source not licensed for public display."}
    # volatility indexes
    vols, meta["vix"] = {}, {}
    for v in ("^VIX", "^VIX3M"):
        vb = _maybe(v)
        if vb is None:
            continue
        s = vb["close"]
        if provisional and (vp := provisional_bar(v)) is not None:
            s = pd.concat([s, pd.Series([vp["close"]], index=[vp["date"]])])
            meta["vix"][v + "_live"] = {k: vp[k] for k in ("provider", "status", "as_of")}
        vols[v] = s
        sources.add(vb.attrs["provider"])
        meta["vix"][v] = {k: vb.attrs.get(k) for k in ("provider", "authority", "last", "fetched_at")}
    # macro (point-in-time; blocked or stale series come back as NaN)
    used: dict[str, str] = {}

    def pit(sid, transform=None):
        s, tag = macro.pit(sid, f.index, transform)
        if tag:
            used[sid] = tag
        return s

    f = f.join(sentiment_frame(f.index, vols.get("^VIX"), vols.get("^VIX3M"))).join(macro_frame(f.index, pit))
    if (tlt := _maybe("TLT")) is not None:
        f["stock_bond_20"] = f["roc20"] - tlt["close"].reindex(f.index).pct_change(20, fill_method=None)
        sources.add(tlt.attrs["provider"])
    f = f.join(fear_greed(f)).join(macro_risk(f))
    sources |= set(used.values())
    meta["series"] = used
    meta["sources"] = sorted(sources)
    sf = scores.score_frame(f)
    return f, sf, meta


def _daily_status(meta: dict) -> tuple[str, str]:
    """Status of a value computed from confirmed daily bars."""
    p = meta["price"]
    if p.get("stale"):
        return str(Status.STALE), "provider refresh failed; showing the last verified history"
    if not p.get("complete_through_last_session"):
        return str(Status.STALE), f"provider has not published {cal.last_completed_session().isoformat()} yet (last bar {p.get('last')})"
    if cal.session_at() == cal.Session.CLOSED:
        return str(Status.CLOSED), f"market closed; as of the {p.get('last')} close"
    return str(Status.DELAYED), f"as of the {p.get('last')} close (no live intraday value)"


def _card(label, value, *, status, basis, as_of, provider, method, **extra):
    return {"label": label, "value": value, "status": status, "basis": basis, "as_of": as_of, "provider": provider, "method": method,
            "available": value is not None, **extra}


def overview(symbol: str | None = None) -> dict:
    symbol = (symbol or default_benchmark()).upper()
    f, sf, meta = market_frame(symbol)
    last, srow = f.iloc[-1], sf.iloc[-1]
    as_of = f.index[-1].date().isoformat()
    dstatus, dnote = _daily_status(meta)
    prov = live_scores.get(symbol)
    if prov and prov.get("session_date") != (cal.current_session_date() or date.min).isoformat():
        prov = None
    pp = meta["price"]["provider"]

    def score_card(key, label, method, **extra):
        conf = _num(srow[key], 1)
        if prov and prov.get(key) is not None:
            return _card(label, prov[key], status=prov["status"], basis="provisional", as_of=prov["as_of"], provider=",".join(prov["sources"]),
                         method=method, confirmed=conf, confirmed_as_of=as_of, note="Intraday value with today's live bar; validated statistics use closes.",
                         kind="heuristic", **extra)
        return _card(label, conf, status=dstatus, basis="confirmed", as_of=as_of, provider=",".join(meta["sources"]), method=method, note=dnote,
                     kind="heuristic", **extra)

    cards = {
        "bottom": score_card("bottom", "Bottom Opportunity Score", "Regime-weighted mean of point-in-time percentile components", coverage=_num(srow["bottom_cov"], 2)),
        "top": score_card("top", "Top Risk Score", "Regime-weighted mean of point-in-time percentile components", coverage=_num(srow["top_cov"], 2)),
        "trend": score_card("trend", "Trend Confidence", "Mean of ADX strength, MA alignment, 63d R-squared, persistence, breadth", direction=srow["trend_dir"]),
    }
    conf_regime = srow["regime"] if isinstance(srow["regime"], str) else None
    regime = prov["regime"] if prov and prov.get("regime") else conf_regime
    rmethod = "Rules on trend, 200-day slope, drawdown, VIX and breadth (docs/METHODOLOGY.md)"
    cards["regime"] = (_card("Market regime", regime, status=prov["status"], basis="provisional", as_of=prov["as_of"], provider=",".join(prov["sources"]),
                             method=rmethod, confirmed=conf_regime, confirmed_as_of=as_of) if prov and prov.get("regime") else
                       _card("Market regime", regime, status=dstatus, basis="confirmed", as_of=as_of, provider=",".join(meta["sources"]), method=rmethod, note=dnote))
    cards["regime"] |= {"display": scores.REGIMES.get(regime, "Unavailable"), "reasons": scores.regime_reasons(f)}
    # VIX: live index value when a licensed index feed exists, else Cboe's official close
    vq = hub.view("^VIX") if "^VIX" in hub.states else None
    vm = meta["vix"].get("^VIX")
    if vq and vq.get("status") in ("LIVE", "DELAYED", "STALE") and vq.get("last") is not None:
        cards["vix"] = _card("VIX", vq["last"], status=vq["status"], basis="live", as_of=vq["last_ts"], provider=vq["provider"],
                             method="Cboe VIX index value", percentile=_num(expanding_pct(f["vix"]).iloc[-1], 3) if "vix" in f else None)
    else:
        cards["vix"] = _card("VIX", _num(last.get("vix"), 2) if vm else None, status=dstatus if vm else str(Status.UNAVAILABLE),
                             basis="confirmed", as_of=vm and vm["last"], provider=vm and vm["provider"], method="Cboe VIX daily close",
                             note=None if vm else "No VIX source configured or licensed.",
                             percentile=_num(expanding_pct(f["vix"]).iloc[-1], 3) if "vix" in f else None, term_ratio=_num(last.get("vix_term"), 3))
    bi = meta["breadth"]
    bprov = breadth.provisional if bi.get("available") else None
    if bprov:
        r = bprov["row"]
        cards["breadth"] = _card("% S&P 500 above 50-day", _num(r.get("pct_above_50"), 1), status="DELAYED", basis="provisional",
                                 as_of=bprov["as_of"], provider=bprov["provider"], method=f"Constituent snapshots polled every {get_settings().breadth_refresh_s}s",
                                 pct_above_200=_num(r.get("pct_above_200"), 1), mcclellan=_num(r.get("mcclellan_osc"), 1), nhnl=_num(r.get("nhnl_pct"), 1), note=bi.get("note"))
    else:
        cards["breadth"] = _card("% S&P 500 above 50-day", _num(last.get("pct_above_50"), 1), status=dstatus if bi.get("available") else str(Status.UNAVAILABLE),
                                 basis="confirmed", as_of=bi.get("last"), provider=",".join(bi.get("providers", [])),
                                 method="S&P 500 members' daily closes vs their 50-day averages", note=bi.get("note"),
                                 pct_above_200=_num(last.get("pct_above_200"), 1), mcclellan=_num(last.get("mcclellan_osc"), 1), nhnl=_num(last.get("nhnl_pct"), 1))
    fgn = int(last.get("fear_greed_n") or 0)
    cards["sentiment"] = _card("Fear & Greed (reconstructed)", _num(last.get("fear_greed"), 1), status=dstatus if fgn else str(Status.UNAVAILABLE),
                               basis="confirmed", as_of=as_of, provider=",".join(meta["sources"]),
                               method=f"Mean of {fgn} available point-in-time percentile inputs (CNN's published design; not CNN's index)",
                               components={k[3:]: _num(last[k], 1) for k in f.columns if k.startswith("fg:")})
    mstat = macro.status()
    current = [m for m in mstat if m["current"]]
    cards["macro"] = _card("Macro Risk", _num(last.get("macro_risk"), 1),
                           status=str(Status.UNAVAILABLE) if pd.isna(last.get("macro_risk")) else str(Status.DELAYED if len(current) == len(mstat) else Status.STALE),
                           basis="official releases", as_of=max((m["last_obs"] for m in current), default=None), provider=",".join(sorted(set(meta["series"].values()))) or "fred",
                           method="Mean of component scores from FRED series as known on each date (first-release vintages with FRED_API_KEY)",
                           components={k[3:]: _num(last[k], 2) for k in f.columns if k.startswith("mr:")},
                           note=f"{len(current)}/{len(mstat)} series current" + ("" if get_settings().fred_api_key else "; no FRED_API_KEY: latest vintages + publication lags"))
    return clean({
        "symbol": symbol,
        "session": cal.info(),
        "quote": hub.view(symbol) if symbol in hub.states else None,
        "confirmed_as_of": as_of,
        "provisional_as_of": prov and prov["as_of"],
        "price": {"close": _num(last["close"], 2), "change_1d": _num(last["ret1"], 4), "provider": pp, "authority": meta["price"].get("authority"),
                  "adjustment": meta["price"].get("adjustment"), "status": str(dstatus), "note": dnote},
        "cards": cards,
        "explain": {"bottom": scores.explain(sf, f, side="bottom"), "top": scores.explain(sf, f, side="top"),
                    "trend": {k[3:]: _num(srow[k], 3) for k in sf.columns if k.startswith("tc:")}},
        "probabilities": _frames.get_or_compute(f"prob:{symbol}:{_versions(symbol)}", lambda: (score_probabilities(sf, f["close"]), 3600)),
        "meta": meta,
        "limitations": LIMITATIONS,
        "licensing": licensing.describe(),
    })


def refresh_live_scores(symbol: str) -> dict | None:
    """Provisional scores with today's live bar (called by the server's refresh loop, not per tick)."""
    res = market_frame(symbol, provisional=True)
    if res is None:
        with _live_lock:
            live_scores.pop(symbol, None)
        return None
    f, sf, meta = res
    row = sf.iloc[-1]
    snap = {
        "symbol": symbol, "as_of": meta["provisional"]["as_of"], "status": meta["provisional"]["status"],
        "session_date": f.index[-1].date().isoformat(), "sources": meta["sources"],
        "bottom": _num(row["bottom"], 1), "top": _num(row["top"], 1), "trend": _num(row["trend"], 1),
        "regime": row["regime"] if isinstance(row["regime"], str) else None,
        "price": _num(f["close"].iloc[-1], 2), "rsi14": _num(f["rsi14"].iloc[-1], 1),
    }
    with _live_lock:
        live_scores[symbol] = snap
    return snap


def chart(symbol: str) -> dict:
    symbol = symbol.upper()
    b = bars(symbol)
    pb = provisional_bar(symbol)
    if pb:
        licensing.check([pb["provider"]], f"{symbol} live price")
    full = _append(b, pb)
    f = technical_frame(full)
    lo_anchor = full["close"].tail(252).idxmin()
    return clean({
        "symbol": symbol, "provider": b.attrs["provider"], "authority": b.attrs["authority"], "adjustment": b.attrs["adjustment"],
        "last_confirmed": b.attrs["last"], "provisional": pb and {k: pb[k] for k in ("provider", "feed", "status", "as_of")},
        "bars": [{"t": d.date().isoformat(), "o": _num(r.open, 4), "h": _num(r.high, 4), "l": _num(r.low, 4), "c": _num(r.close, 4), "v": _num(r.volume, 0)}
                 for d, r in full.iterrows()],
        "overlays": {k: [_num(v, 4) for v in f[k]] for k in ("sma20", "sma50", "sma200", "bb_upper", "bb_lower")}
        | {"avwap_from_252d_low": [_num(v, 4) for v in anchored_vwap(full, lo_anchor)]},
        "avwap_anchor": lo_anchor.date().isoformat(),
        "volume_profile": volume_profile(full),
    })


def intraday(symbol: str) -> dict:
    """Today's (or the last session's) 1-minute bars: live hub bars when streaming, else the provider's REST bars."""
    symbol = symbol.upper()
    st = hub.states.get(symbol)
    if st is not None and st.seeded:
        status, reason = hub.status(st)
        if status != Status.UNAVAILABLE:
            return clean({"symbol": symbol, "status": str(status), "reason": reason, "provider": st.provider, "feed": st.feed,
                          "session_date": st.session_date, "bars": hub.bars(symbol), "vwap": st.vwap()})
    up = hub.active
    if up is None:
        raise Unavailable("Intraday bars need a streaming provider (Alpaca or Massive keys).")
    licensing.check([up.name], f"{symbol} intraday prices")
    now = datetime.now(timezone.utc)
    day = cal.current_session_date(now) or cal.last_completed_session(now)
    rows = history.intraday(symbol, up.name, day)
    return clean({"symbol": symbol, "status": str(Status.CLOSED if cal.session_at() == cal.Session.CLOSED else Status.DELAYED),
                  "reason": "REST minute bars (subscribe for live updates)", "provider": up.name, "feed": up.feed, "session_date": day,
                  "bars": [b.to_json() for b in rows]})


INDICATOR_COLUMNS = {
    "rsi14": "RSI(14)", "macd": "MACD", "macd_hist": "MACD histogram", "stochrsi_k": "Stoch RSI %K", "bb_pctb": "Bollinger %B",
    "adx": "ADX", "atr_pct": "ATR %", "hv20": "Hist. vol (20d)", "dist200": "Distance from 200d", "drawdown": "Drawdown",
    "rs63": "Relative strength vs benchmark (63d)", "obv": "On-balance volume", "pct_above_50": "% above 50d", "pct_above_200": "% above 200d",
    "mcclellan_osc": "McClellan Oscillator", "mcclellan_sum": "McClellan Summation", "nhnl_pct": "Net new highs %", "pct_oversold": "% oversold",
    "ad_line": "Advance/decline line", "vix": "VIX", "vix_term": "VIX / VIX3M", "fear_greed": "Fear & Greed (recon.)",
    "curve_10y2y": "10Y-2Y curve", "credit_spread": "Credit spread", "real_10y": "10Y real yield", "fed_funds": "Fed funds",
    "nfci": "NFCI", "sahm": "Sahm gap", "core_cpi_yoy": "Core CPI YoY", "net_liquidity": "Net liquidity ($bn)", "macro_risk": "Macro risk",
    "bottom": "Bottom Opportunity Score", "top": "Top Risk Score", "trend": "Trend Confidence",
}


def indicator_series(symbol: str, names: list[str]) -> dict:
    f, sf, meta = market_frame(symbol)
    both = f.join(sf[["bottom", "top", "trend"]])
    out = {"symbol": symbol.upper(), "t": [d.date().isoformat() for d in both.index], "series": {}, "labels": {}, "basis": "confirmed",
           "as_of": both.index[-1].date().isoformat(), "sources": meta["sources"]}
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


def signals(symbol: str, bottom_th: float = 70, top_th: float = 70) -> dict:
    symbol = symbol.upper()

    def compute():
        f, sf, _ = market_frame(symbol)
        close = f["close"]
        events = []

        def fwd(d):
            i = f.index.get_loc(d)
            return {str(h): _num(close.iloc[i + h] / close.iloc[i] - 1) if i + h < len(close) else None for h in (20, 60, 126)}

        for side, th in (("bottom", bottom_th), ("top", top_th)):
            for d in _declustered_crosses(sf[side], th, 20):
                ex = scores.explain(sf, f, d, side)
                events.append({"date": d.date().isoformat(), "type": side, "label": f"{'Bottom score' if side == 'bottom' else 'Top risk'} {ex['score']}",
                               "score": ex["score"], "regime": ex["regime"], "triggered_by": [c for c in ex["components"] if c["available"]][:6],
                               "forward_realized": fwd(d)})
        _, _, div = divergences(close, f["rsi14"])
        for e in div:
            events.append({"date": e["confirmed"], "type": f"{e['type']}_divergence", "label": f"RSI {e['type']} divergence",
                           "triggered_by": [{"label": "Price pivots", "value": e["price"]}, {"label": "RSI at pivots", "value": e["osc"]},
                                            {"label": "Pivot dates", "value": [e["pivot1"], e["pivot2"]]}], "forward_realized": fwd(pd.Timestamp(e["confirmed"]))})
        if "zweig_thrust" in f:
            for d in f.index[f["zweig_thrust"].fillna(False).astype(bool)]:
                events.append({"date": d.date().isoformat(), "type": "zweig_thrust", "label": "Zweig breadth thrust",
                               "triggered_by": [{"label": "10d EMA of A/(A+D)", "value": _num(f.at[d, "zweig_ema"], 3)}], "forward_realized": fwd(d)})
        reg = sf["regime"]
        for d in reg.index[(reg != reg.shift()) & reg.isin(["bear_capitulation", "distribution"])]:
            events.append({"date": d.date().isoformat(), "type": f"regime_{reg[d]}", "label": scores.REGIMES[reg[d]],
                           "triggered_by": [{"label": r} for r in scores.regime_reasons(f, f.index.get_loc(d))], "forward_realized": fwd(d)})
        events.sort(key=lambda e: e["date"])
        return clean({"symbol": symbol, "thresholds": {"bottom": bottom_th, "top": top_th}, "events": events, "basis": "confirmed closes",
                      "note": "Signals use data available on each date. 'forward_realized' shows what happened afterwards (evaluation only)."}), 3600

    return _frames.get_or_compute(f"signals:{symbol}:{bottom_th}:{top_th}:{_versions(symbol)}", compute)


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


def analyze(symbol: str) -> dict:
    symbol = symbol.upper()
    b = bars(symbol)
    bench = default_benchmark()
    bb = _maybe(bench)
    bench_close = bb["close"] if bb is not None else None

    def confirmed():
        f = technical_frame(b, bench_close)
        if len(f) < 260:
            raise Unavailable(f"{symbol}: need at least 260 sessions of history (provider has {len(f)})")
        state, reasons = classify_stock(f)
        hist = state_history(f)
        stats = {}
        for h in (20, 60, 126):
            fr = fwd_return(f["close"], h).reindex(hist.index)
            vals = fr[hist == state].dropna().values
            stats[str(h)] = {"n_samples": int(len(vals)), "median": _num(np.median(vals)) if len(vals) else None,
                             "hit_rate": _num((vals > 0).mean()) if len(vals) else None,
                             "unconditional_median": _num(fr.dropna().median()), "unconditional_hit_rate": _num((fr.dropna() > 0).mean())}
        _, _, div = divergences(f["close"], f["rsi14"])
        return (f, state, reasons, stats, div), 3600

    f, state, reasons, stats, div = _frames.get_or_compute(f"an:{symbol}:{history.version(symbol)}:{history.version(bench)}", confirmed)
    r = f.iloc[-1]
    keys = ("close", "sma20", "sma50", "sma200", "dist200", "rsi14", "macd_hist", "stochrsi_k", "bb_pctb", "adx", "atr_pct", "hv20", "drawdown",
            "roc20", "roc63", "vol_ratio", "rs63", "rs126", "r2_63")
    pb = provisional_bar(symbol)
    live = None
    if pb is not None and not licensing.blocked([pb["provider"]]):
        fp = technical_frame(_append(b, pb), _append(bb, provisional_bar(bench))["close"] if bb is not None else None)
        pstate, preasons = classify_stock(fp)
        live = {"as_of": pb["as_of"], "status": pb["status"], "provider": pb["provider"], "state": pstate, "state_label": STATE_LABELS[pstate],
                "indicators": {k: _num(fp.iloc[-1].get(k)) for k in keys}}
    prof = reference.profile(symbol)
    sector = None
    if prof and prof.get("sector_etf") and prof["sector_etf"] != symbol and (sb := _maybe(prof["sector_etf"])) is not None:
        sector = {"etf": prof["sector_etf"], "sector": prof["sector"],
                  "sector_rs63_vs_benchmark": _num((sb["close"] / bench_close.reindex(sb.index)).pct_change(63).iloc[-1]) if bench_close is not None else None,
                  "stock_rs63_vs_sector": _num((b["close"] / sb["close"].reindex(b.index)).pct_change(63).iloc[-1])}
    try:
        fundamentals = reference.fundamentals(symbol)
        licensing.check(["fmp"], "Fundamentals")
    except Unavailable as e:
        fundamentals = {"unavailable": [str(e)]}
    try:
        options = options_summary(symbol, live and live["indicators"].get("close")) if prof is None or prof.get("is_etf") or symbol in ("SPY", "QQQ") else None
    except Unavailable as e:
        options = {"unavailable": str(e)}
    market = None
    try:
        ov = overview(bench)
        market = {"benchmark": bench, "regime": ov["cards"]["regime"]["display"], "bottom": ov["cards"]["bottom"]["value"], "top": ov["cards"]["top"]["value"],
                  "basis": ov["cards"]["bottom"]["basis"]}
    except Unavailable:
        pass
    return clean({
        "symbol": symbol, "name": (prof or {}).get("name") or symbol, "profile": prof,
        "as_of": f.index[-1].date().isoformat(), "provider": b.attrs["provider"], "authority": b.attrs["authority"], "adjustment": b.attrs["adjustment"],
        "status": (ds := _daily_status({"price": b.attrs}))[0], "status_note": ds[1],
        "quote": hub.view(symbol) if symbol in hub.states else None,
        "state": state, "state_label": STATE_LABELS[state], "reasons": reasons, "live": live,
        "state_forward_stats": stats,
        "state_stats_note": "Forward returns after past dates in the same state on this symbol (confirmed closes). Consecutive days overlap, so n_samples overstates independent evidence.",
        "indicators": {k: _num(r.get(k)) for k in keys},
        "percentiles": {k: _num(expanding_pct(f[k]).iloc[-1], 3) for k in ("rsi14", "dist200", "drawdown", "hv20", "rs126") if k in f},
        "recent_divergences": div[-5:], "sector": sector, "market": market, "fundamentals": fundamentals, "options": options, "benchmark": bench,
    })


def symbols() -> dict:
    st = get_settings()
    return {"benchmarks": st.benchmark_list, "universe": st.universe, "default": default_benchmark()}


def status() -> dict:
    st = get_settings()
    prices = []
    for sym in dict.fromkeys(st.benchmark_list + ["^VIX", "^VIX3M", "TLT"]):
        df = history._cache.peek(f"d:{sym}")
        prices.append({"symbol": sym, "loaded": df is not None} | ({k: df.attrs.get(k) for k in ("provider", "authority", "adjustment", "first", "last", "fetched_at",
                                                                                                    "complete_through_last_session", "issues", "provider_errors")}
                                                                   if df is not None else {}))
    return clean({
        "session": cal.info(),
        "licensing": licensing.describe(),
        "streams": hub.providers_status(),
        "live": [hub.view(s) for s in sorted(hub.states)],
        "providers": {
            "alpaca": {"configured": st.has_alpaca, "feed": st.alpaca_feed, "roles": ["stream", "snapshots", "history (2016+)", "breadth prices"]},
            "massive": {"configured": bool(st.massive_api_key), "realtime": st.massive_realtime, "indices": st.massive_indices, "options": st.massive_options,
                        "roles": ["stream", "snapshots", "history", "VIX live (Indices)", "options analytics (Options)", "symbol search"]},
            "tiingo": {"configured": bool(st.tiingo_api_key), "roles": ["long dividend-adjusted daily history"]},
            "fred": {"configured": True, "api_key": bool(st.fred_api_key), "roles": ["macro series (first-release vintages with key)"]},
            "fmp": {"configured": bool(st.fmp_api_key), "roles": ["profiles, market cap, sector", "fundamentals", "S&P 500 membership", "search"]},
            "cboe": {"configured": True, "roles": ["official VIX/VIX3M/VIX9D/VVIX daily history"]},
            "anthropic": {"configured": bool(st.anthropic_api_key), "roles": ["AI analyst"]},
        },
        "history": prices,
        "macro": macro.status(),
        "breadth": breadth.info | {"provisional_as_of": breadth.provisional and breadth.provisional["as_of"]},
        "limitations": LIMITATIONS,
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
