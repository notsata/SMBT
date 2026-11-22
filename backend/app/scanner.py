"""Opportunity scanner: current setups across the universe, ranked by historically measured evidence.

Evidence per flag = pooled forward 20-session return after past occurrences of that flag across all scanned
symbols, minus each symbol's unconditional mean (no fitting, just counting). The universe is today's list,
so pooled evidence carries survivorship bias; the response says so.
"""

import numpy as np
import pandas as pd

from . import services as S
from .config import get_settings
from .market import history, reference
from .market.cache import TTLCache
from .market.model import Unavailable
from .quant.technical import expanding_pct, recent, technical_frame

_cache = TTLCache(maxsize=4)

H = 20

FLAGS = {
    # id: (label, direction, fn(f) -> bool Series)
    "oversold_confirmed": ("Extreme oversold with bullish confirmation", "bullish",
                           lambda f: recent(f["rsi14"] < 30, 10) & (f["rsi14"] > f["rsi14"].shift()) & (f["close"] > f["close"].shift())),
    "bullish_divergence": ("Bullish RSI/MACD divergence", "bullish", lambda f: recent(f["rsi_bull_div"] | f["macd_bull_div"], 5)),
    "capitulation_volume": ("Capitulation volume", "bullish",
                            lambda f: recent((f["vol_ratio"] >= 2.5) & (f["ret1"] < -0.03), 5) & f["vol_ratio"].notna()),
    "support_test": ("Test of rising 200-day support", "bullish",
                     lambda f: (f["close"] > f["sma200"]) & ((f["close"] - f["sma200"]) < f["atr14"]) & (f["sma200_slope"] > 0)),
    "trend_reversal_up": ("Trend reversal up (reclaimed 50-day)", "bullish",
                          lambda f: recent((f["close"] > f["sma50"]) & ((f["close"] < f["sma50"]).astype(float).shift().rolling(20).min() == 1), 5)),
    "bearish_divergence_high": ("Bearish divergence near highs", "bearish",
                                lambda f: recent(f["rsi_bear_div"] | f["macd_bear_div"], 5) & (f["drawdown"] > -0.05)),
    "unsustainable_extension": ("Unsustainable extension above 200-day", "bearish", lambda f: expanding_pct(f["dist200"]) >= 0.97),
    "overbought_climax": ("Overbought on climax volume", "bearish", lambda f: recent((f["rsi14"] > 80) & (f["vol_ratio"] > 2), 5)),
    "trend_reversal_down": ("Trend reversal down (lost 50-day)", "bearish",
                            lambda f: recent((f["close"] < f["sma50"]) & ((f["close"] > f["sma50"]).astype(float).shift().rolling(40).min() == 1), 5)),
}


def weekly(b: pd.DataFrame) -> pd.DataFrame:
    w = b.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    if len(w) and b.index[-1].weekday() != 4:  # drop the in-progress week
        w = w.iloc[:-1]
    w.attrs = b.attrs
    return w


def _compute(timeframe: str) -> dict:
    bench = S.default_benchmark()
    bench_bars = S.bars(bench)
    bench_close = (weekly(bench_bars) if timeframe == "weekly" else bench_bars)["close"]
    rows, pooled, skipped, providers = [], {k: [] for k in FLAGS}, [], set()
    for sym in get_settings().universe:
        try:
            b = S.bars(sym)
        except Unavailable as e:
            skipped.append({"symbol": sym, "reason": str(e)[:200]})
            continue
        providers.add(b.attrs["provider"])
        if timeframe == "weekly":
            b = weekly(b)
        if len(b) < 260:
            skipped.append({"symbol": sym, "reason": f"only {len(b)} bars of history"})
            continue
        prof = reference.profile(sym) or {}
        f = technical_frame(b, bench_close if sym != bench else None)
        fwd = f["close"].shift(-H) / f["close"] - 1
        base = fwd.mean()
        active, evidence_local = [], {}
        for fid, (_, _, fn) in FLAGS.items():
            sig = fn(f).fillna(False).astype(bool)
            onset = sig & ~sig.shift(fill_value=False)
            ex = (fwd[onset] - base).dropna()
            pooled[fid].extend(ex.tolist())
            if sig.iloc[-1]:
                active.append(fid)
                evidence_local[fid] = {"n": int(len(ex)), "excess_mean": float(ex.mean()) if len(ex) else None}
        r = f.iloc[-1]
        rows.append({
            "symbol": sym, "name": prof.get("name", ""), "sector": prof.get("sector", ""), "kind": "etf" if prof.get("is_etf") else ("stock" if prof else ""),
            "cap_bucket": prof.get("cap_bucket", ""), "market_cap": prof.get("market_cap"), "provider": b.attrs["provider"],
            "as_of": f.index[-1].date().isoformat(), "close": S._num(r.close, 2), "rsi14": S._num(r.rsi14, 1), "dist200": S._num(r.dist200),
            "drawdown": S._num(r.drawdown), "rs63": S._num(r.get("rs63")), "vol_ratio": S._num(r.vol_ratio, 2),
            "flags": active, "evidence_symbol": evidence_local,
        })
    pooled_stats = {}
    for fid, vals in pooled.items():
        v = np.array(vals, dtype=float)
        pooled_stats[fid] = {
            "label": FLAGS[fid][0], "direction": FLAGS[fid][1], "n": int(len(v)),
            "excess_mean": float(v.mean()) if len(v) else None,
            "excess_hit_rate": float((v > 0).mean()) if len(v) else None,
            "t_stat": float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 10 and v.std() > 0 else None,
        }
    for row in rows:
        for d in ("bullish", "bearish"):
            act = [x for x in row["flags"] if FLAGS[x][1] == d]
            sign = 1 if d == "bullish" else -1
            # Evidence-weighted rank: only flags whose pooled history pointed the right way (n >= 30) add score.
            ev = sum(max(0.0, sign * (pooled_stats[x]["excess_mean"] or 0)) for x in act if pooled_stats[x]["n"] >= 30)
            row[f"{d}_confluence"] = len(act)
            row[f"{d}_evidence"] = round(ev * 100, 3)
    return {"timeframe": timeframe, "horizon": H, "benchmark": bench, "rows": rows, "flag_evidence": pooled_stats, "skipped": skipped,
            "providers": sorted(providers), "basis": "confirmed closes", "profiles": "fmp" if get_settings().fmp_api_key else None,
            "as_of": bench_bars.attrs["last"],
            "note": f"Evidence = pooled mean {H}-bar forward return after past flag onsets minus each symbol's unconditional mean. "
                    "Universe is today's list (survivorship bias); overlapping windows across correlated symbols overstate t-stats. "
                    "Sector and market cap come from live FMP profiles when configured."}


def scan(timeframe: str = "daily", direction: str = "bullish", sector: str = "", kind: str = "", cap: str = "",
         signal: str = "", query: str = "", limit: int = 100) -> dict:
    key = f"scan:{timeframe}:{history.version(S.default_benchmark())}"
    data = _cache.get_or_compute(key, lambda: (_compute(timeframe), 3600))
    rows = data["rows"]
    if sector:
        rows = [r for r in rows if r["sector"].lower() == sector.lower()]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if cap:
        rows = [r for r in rows if r["cap_bucket"] == cap]
    if signal:
        rows = [r for r in rows if signal in r["flags"]]
    if query:
        rows = [r for r in rows if query.upper() in r["symbol"] or query.lower() in r["name"].lower()]
    if direction in ("bullish", "bearish"):
        rows = sorted(rows, key=lambda r: (-r[f"{direction}_confluence"], -r[f"{direction}_evidence"], r["symbol"]))
    sectors = sorted({r["sector"] for r in data["rows"] if r["sector"]})
    return S.clean({**data, "rows": rows[:limit], "total": len(rows), "sectors": sectors, "flags": {k: {"label": v[0], "direction": v[1]} for k, v in FLAGS.items()}})
