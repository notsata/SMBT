"""Regime classification and the three headline heuristic scores.

These are HEURISTIC scores: weighted averages of point-in-time percentile ranks and flags. They are not
probabilities. Their historical predictive value is measured separately (probability.py, backtest/), and the
API reports both side by side so a high score is never presented as a forecast.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .technical import expanding_pct, recent

REGIMES = {
    "strong_bull": "Strong bull market",
    "bull_correction": "Bull market correction",
    "sideways": "Sideways consolidation",
    "early_bear": "Early bear market",
    "bear_capitulation": "Bear market capitulation",
    "recovery": "Recovery / accumulation",
    "distribution": "Potential distribution / market top",
}


def col(f: pd.DataFrame, name: str) -> pd.Series:
    return f[name] if name in f else pd.Series(np.nan, index=f.index)


def flag(f: pd.DataFrame, name: str) -> pd.Series:
    return f[name].fillna(False).astype(bool) if name in f else pd.Series(False, index=f.index)


def _breadth_div(f: pd.DataFrame) -> pd.Series:
    p50 = col(f, "pct_above_50")
    near_high = col(f, "drawdown") > -0.03
    return ((-(p50 - p50.shift(63)) / 30).clip(0, 1)).where(p50.notna()) * near_high


def classify_regime(f: pd.DataFrame) -> pd.Series:
    c, dd, slope = f["close"], f["drawdown"], f["sma200_slope"]
    above200 = c > f["sma200"]
    vix_p = expanding_pct(col(f, "vix"))
    capit = (dd <= -0.20) & (
        (vix_p >= 0.90) | (f["rsi14"] < 30) | (col(f, "pct_oversold") >= 40) | (col(f, "pct_above_50") <= 15)
    )
    was_bear = ((dd <= -0.20) | (~above200 & (slope < 0))).astype(float).rolling(126, min_periods=1).max().astype(bool)
    sma50_up = f["sma50"].diff(10) > 0
    recovery = was_bear & (dd <= -0.08) & (c > f["sma50"]) & sma50_up & (f["roc20"] > 0.04)
    early_bear = ~above200 & (slope <= 0) & (dd <= -0.10)
    warnings = recent(flag(f, "rsi_bear_div") | flag(f, "macd_bear_div"), 15) | recent(flag(f, "vp_bear_div"), 10) | (_breadth_div(f) >= 0.5)
    distribution = above200 & (dd > -0.05) & (expanding_pct(f["dist200"]) >= 0.85) & warnings
    strong_bull = above200 & (slope > 0.005) & (f["sma50"] > f["sma200"]) & (dd > -0.05)
    bull_corr = (slope > -0.002) & (dd <= -0.05) & (dd > -0.20)
    conds = [capit, recovery, early_bear, distribution, strong_bull, bull_corr]
    names = ["bear_capitulation", "recovery", "early_bear", "distribution", "strong_bull", "bull_correction"]
    out = pd.Series(np.select([x.fillna(False).values for x in conds], names, "sideways"), index=f.index)
    return out.where(f["sma200"].notna() & dd.notna())


def regime_reasons(f: pd.DataFrame, i: int = -1) -> list[str]:
    r = f.iloc[i]
    out = [
        f"Close {'above' if r.close > r.sma200 else 'below'} 200-day average ({r.dist200:+.1%})",
        f"200-day average {'rising' if r.sma200_slope > 0 else 'falling'} ({r.sma200_slope:+.2%} over 20d)",
        f"Drawdown from 52-week high {r.drawdown:.1%}",
        f"RSI(14) {r.rsi14:.0f}",
    ]
    if "pct_above_50" in f and pd.notna(r.get("pct_above_50")):
        out.append(f"{r.pct_above_50:.0f}% of S&P 500 members above 50-day average")
    if "vix" in f and pd.notna(r.get("vix")):
        out.append(f"VIX {r.vix:.1f}")
    return out


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    category: str  # technical | breadth | sentiment | macro
    fn: Callable[[pd.DataFrame], pd.Series]
    detail: str


P = expanding_pct

BOTTOM: list[Component] = [
    Component("rsi_low", "RSI(14) low vs history", "technical", lambda f: 1 - P(f["rsi14"]), "1 - percentile of RSI(14)"),
    Component("drawdown", "Depth of drawdown", "technical", lambda f: 1 - P(f["drawdown"]), "1 - percentile of drawdown from 52w high"),
    Component("below_200", "Distance below 200-day", "technical", lambda f: 1 - P(f["dist200"]), "1 - percentile of distance from 200d MA"),
    Component("bb_low", "Bollinger %B low", "technical", lambda f: 1 - P(f["bb_pctb"]), "1 - percentile of %B(20,2)"),
    Component("bull_div", "Bullish RSI/MACD divergence (10d)", "technical",
              lambda f: recent(flag(f, "rsi_bull_div") | flag(f, "macd_bull_div"), 10).astype(float), "confirmed pivot divergence"),
    Component("capitulation_vol", "Capitulation volume (5d)", "technical",
              lambda f: recent((col(f, "vol_ratio") >= 2.0) & (f["ret1"] < -0.02), 5).astype(float).where(col(f, "vol_ratio").notna()),
              "volume >= 2x 50d avg on a >2% down day"),
    Component("breadth_low", "Few members above 50-day", "breadth", lambda f: 1 - col(f, "pct_above_50") / 100, "1 - % above 50d MA"),
    Component("oversold_share", "Share of members oversold", "breadth", lambda f: (col(f, "pct_oversold") / 50).clip(0, 1), "% members RSI<30, /50"),
    Component("mcclellan_low", "McClellan Oscillator low", "breadth", lambda f: 1 - P(col(f, "mcclellan_osc")), "1 - percentile"),
    Component("nhnl_low", "Net new lows", "breadth", lambda f: 1 - P(col(f, "nhnl_pct")), "1 - percentile of (NH-NL)%"),
    Component("zweig", "Zweig breadth thrust (20d)", "breadth",
              lambda f: recent(flag(f, "zweig_thrust"), 20).astype(float).where(col(f, "zweig_ema").notna()), "thrust fired"),
    Component("washout", "90% down day (10d)", "breadth",
              lambda f: recent(flag(f, "washout_day"), 10).astype(float).where(col(f, "zweig_ema").notna()), ">=90% decliners"),
    Component("vix_high", "VIX elevated", "sentiment", lambda f: P(col(f, "vix")), "percentile of VIX"),
    Component("vix_backwardation", "VIX term structure inverted", "sentiment", lambda f: P(col(f, "vix_term")), "percentile of VIX/VIX3M"),
    Component("putcall_high", "Put/call elevated", "sentiment", lambda f: P(col(f, "pcr10")), "percentile of 10d equity P/C"),
    Component("aaii_bearish", "AAII bearish", "sentiment", lambda f: 1 - P(col(f, "aaii_spread"), 100), "1 - percentile of bull-bear spread"),
    Component("fear", "Fear & Greed (fear)", "sentiment", lambda f: 1 - col(f, "fear_greed") / 100, "1 - reconstructed F&G/100"),
    Component("hv_high", "Realized volatility high", "sentiment", lambda f: P(f["hv20"]), "percentile of 20d HV"),
    Component("credit_easing", "Credit stress easing from high", "macro",
              lambda f: (P(col(f, "credit_spread")) * (col(f, "credit_chg20") < 0)).where(col(f, "credit_spread").notna()),
              "spread percentile x (spread falling over 20d)"),
]

TOP: list[Component] = [
    Component("extension", "Extension above 200-day", "technical", lambda f: P(f["dist200"]), "percentile of distance from 200d MA"),
    Component("rsi_high", "RSI(14) high vs history", "technical", lambda f: P(f["rsi14"]), "percentile of RSI(14)"),
    Component("atr_extension", "Stretch above 50-day in ATRs", "technical", lambda f: P(f["ext_atr"]), "percentile of (close-50d)/ATR"),
    Component("bear_div", "Bearish divergence near highs (15d)", "technical",
              lambda f: (recent(flag(f, "rsi_bear_div") | flag(f, "macd_bear_div"), 15) & (f["drawdown"] > -0.05)).astype(float), "confirmed pivot divergence"),
    Component("vp_div", "Price up on falling OBV (10d)", "technical", lambda f: recent(flag(f, "vp_bear_div"), 10).astype(float).where(col(f, "obv").notna()), "OBV divergence"),
    Component("breadth_div", "Breadth divergence at highs", "breadth", _breadth_div, "near high while % above 50d fell vs 63d ago"),
    Component("overbought_share", "Share of members overbought", "breadth", lambda f: (col(f, "pct_overbought") / 40).clip(0, 1), "% members RSI>70, /40"),
    Component("nhnl_div", "Net new highs weak near high", "breadth",
              lambda f: ((f["drawdown"] > -0.03) & (col(f, "nhnl_pct").rolling(20).mean() < 0)).astype(float).where(col(f, "nhnl_pct").notna()), "near index high with net new lows"),
    Component("vix_low", "VIX complacency", "sentiment", lambda f: 1 - P(col(f, "vix")), "1 - percentile of VIX"),
    Component("contango", "Steep VIX contango", "sentiment", lambda f: 1 - P(col(f, "vix_term")), "1 - percentile of VIX/VIX3M"),
    Component("putcall_low", "Put/call low", "sentiment", lambda f: 1 - P(col(f, "pcr10")), "1 - percentile of 10d P/C"),
    Component("aaii_bullish", "AAII bullish", "sentiment", lambda f: P(col(f, "aaii_spread"), 100), "percentile of bull-bear spread"),
    Component("greed", "Fear & Greed (greed)", "sentiment", lambda f: col(f, "fear_greed") / 100, "reconstructed F&G/100"),
    Component("uninversion", "Yield curve un-inverting", "macro", lambda f: flag(f, "curve_uninversion").astype(float).where(col(f, "curve_10y2y").notna()), "10y-2y > 0 after >= 60 inverted days in past year"),
    Component("credit_widening", "Credit spreads widening", "macro", lambda f: P(col(f, "credit_chg63")), "percentile of 63d spread change"),
    Component("fed_tightening", "Fed tightening", "macro", lambda f: (col(f, "fed_chg252") / 2.0).clip(0, 1), "12m fed funds change / 200bp"),
    Component("sahm", "Unemployment rising (Sahm)", "macro", lambda f: (col(f, "sahm") / 0.5).clip(0, 1), "Sahm gap / 0.5pp"),
    Component("valuation", "Valuation stretched", "macro",
              lambda f: P(col(f, "cape"), 60).fillna(P(col(f, "mcap_gdp"), 20)), "percentile of CAPE or market cap/GDP"),
]

# Category weights by regime. Bottoms in bull markets are shallow technical dips; in bear markets they are
# breadth/sentiment washouts. Tops are a breadth/sentiment story late in bull markets, macro in late cycle.
BOTTOM_W = {
    "default": {"technical": 0.35, "breadth": 0.25, "sentiment": 0.30, "macro": 0.10},
    "strong_bull": {"technical": 0.50, "breadth": 0.20, "sentiment": 0.25, "macro": 0.05},
    "bull_correction": {"technical": 0.40, "breadth": 0.25, "sentiment": 0.30, "macro": 0.05},
    "early_bear": {"technical": 0.25, "breadth": 0.30, "sentiment": 0.30, "macro": 0.15},
    "bear_capitulation": {"technical": 0.20, "breadth": 0.35, "sentiment": 0.35, "macro": 0.10},
    "recovery": {"technical": 0.30, "breadth": 0.35, "sentiment": 0.25, "macro": 0.10},
}
TOP_W = {
    "default": {"technical": 0.30, "breadth": 0.25, "sentiment": 0.25, "macro": 0.20},
    "strong_bull": {"technical": 0.30, "breadth": 0.25, "sentiment": 0.30, "macro": 0.15},
    "distribution": {"technical": 0.25, "breadth": 0.35, "sentiment": 0.25, "macro": 0.15},
    "bear_capitulation": {"technical": 0.40, "breadth": 0.20, "sentiment": 0.20, "macro": 0.20},
    "early_bear": {"technical": 0.30, "breadth": 0.20, "sentiment": 0.20, "macro": 0.30},
}


def component_frame(f: pd.DataFrame, comps: list[Component]) -> pd.DataFrame:
    return pd.DataFrame({c.id: c.fn(f).astype(float).clip(0, 1) for c in comps}, index=f.index)


def weighted_score(cf: pd.DataFrame, comps: list[Component], regime: pd.Series, weights: dict) -> pd.DataFrame:
    cats = sorted({c.category for c in comps})
    cat_mean = pd.DataFrame({k: cf[[c.id for c in comps if c.category == k]].mean(axis=1) for k in cats})
    w = pd.DataFrame([weights.get(r, weights["default"]) if isinstance(r, str) else weights["default"] for r in regime], index=cf.index)[cats]
    avail = cat_mean.notna()
    wsum = (w * avail).sum(axis=1)
    score = (cat_mean.fillna(0) * w).sum(axis=1) / wsum.replace(0, np.nan) * 100
    coverage = cf.notna().mean(axis=1)
    out = pd.DataFrame({"score": score.where(coverage >= 0.25), "coverage": coverage})
    return out.join(cat_mean.add_prefix("cat:"))


def trend_confidence(f: pd.DataFrame) -> pd.DataFrame:
    up = f["sma200_slope"] >= 0
    d = np.where(up, 1, -1)
    seq = [f["close"], f["sma20"], f["sma50"], f["sma100"], f["sma200"]]
    align = sum(((a - b) * d > 0).astype(float) for a, b in zip(seq[:-1], seq[1:])) / 4
    side = ((f["close"] - f["sma50"]) * d > 0).astype(float).rolling(63, min_periods=40).mean()
    parts = {
        "adx": ((f["adx"] - 10) / 30).clip(0, 1),
        "ma_alignment": align,
        "r2_63": f["r2_63"],
        "persistence": side,
    }
    if "pct_above_200" in f and f["pct_above_200"].notna().any():
        b = f["pct_above_200"] / 100
        parts["breadth_confirm"] = b.where(up, 1 - b)
    df = pd.DataFrame(parts, index=f.index)
    return pd.DataFrame(
        {"trend": df.mean(axis=1).where(df.notna().sum(axis=1) >= 3) * 100, "trend_dir": np.where(up, "up", "down")},
        index=f.index,
    ).join(df.add_prefix("tc:"))


def score_frame(f: pd.DataFrame) -> pd.DataFrame:
    regime = classify_regime(f)
    bcf, tcf = component_frame(f, BOTTOM), component_frame(f, TOP)
    b = weighted_score(bcf, BOTTOM, regime, BOTTOM_W)
    t = weighted_score(tcf, TOP, regime, TOP_W)
    out = pd.DataFrame({"regime": regime, "bottom": b["score"], "bottom_cov": b["coverage"], "top": t["score"], "top_cov": t["coverage"]}, index=f.index)
    out = out.join(trend_confidence(f)).join(bcf.add_prefix("b:")).join(tcf.add_prefix("t:"))
    return out


def explain(sf: pd.DataFrame, f: pd.DataFrame, when=None, side: str = "bottom") -> dict:
    """Component-level breakdown of one score on one date: which indicators drove it."""
    i = sf.index.get_loc(when) if when is not None else len(sf) - 1
    row = sf.iloc[i]
    comps = BOTTOM if side == "bottom" else TOP
    weights = (BOTTOM_W if side == "bottom" else TOP_W)
    w = weights.get(row["regime"], weights["default"]) if isinstance(row["regime"], str) else weights["default"]
    pref = "b:" if side == "bottom" else "t:"
    items = []
    for c in comps:
        v = row[pref + c.id]
        items.append({"id": c.id, "label": c.label, "category": c.category, "value": None if pd.isna(v) else round(float(v), 3),
                      "category_weight": w[c.category], "detail": c.detail, "available": bool(pd.notna(v))})
    items.sort(key=lambda x: -(x["value"] or 0))
    return {
        "date": sf.index[i].date().isoformat(),
        "score": None if pd.isna(row[side]) else round(float(row[side]), 1),
        "coverage": round(float(row[f"{side}_cov"]), 2),
        "regime": row["regime"],
        "components": items,
        "kind": "heuristic",
    }
