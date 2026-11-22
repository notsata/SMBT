"""Strategy definitions: (feature frame, score frame, params) -> target weight decided at each close.

All inputs are causal, so a target at t depends only on data through t. Param grids are deliberately
small: wide grids overfit walk-forward folds too.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def hold_after(entries: pd.Series, days: int) -> pd.Series:
    """1 for `days` sessions starting on each entry signal (re-entries extend the hold)."""
    e = entries.fillna(False).astype(float)
    return (e.rolling(days, min_periods=1).max() > 0).astype(float)


def latch(enter: pd.Series, exit_: pd.Series, start: float = 0.0) -> pd.Series:
    """Stateful on/off: switch on at `enter`, off at `exit_` (exit wins ties)."""
    e, x = enter.fillna(False).values, exit_.fillna(False).values
    out = np.empty(len(e))
    state = start
    for i in range(len(e)):
        if x[i]:
            state = 0.0
        elif e[i]:
            state = 1.0
        out[i] = state
    return pd.Series(out, index=enter.index)


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    description: str
    fn: Callable[[pd.DataFrame, pd.DataFrame, dict], pd.Series]
    grid: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    needs: tuple = ()


def _buy_hold(f, sf, p):
    return pd.Series(1.0, index=f.index)


def _trend200(f, sf, p):
    band = p["band"]
    return latch(f["close"] > f["sma200"] * (1 + band), f["close"] < f["sma200"] * (1 - band), start=1.0)


def _bottom_entries(f, sf, p):
    return hold_after(sf["bottom"] >= p["threshold"], p["hold"])


def _top_overlay(f, sf, p):
    off = sf["top"] >= p["threshold"]
    on = sf["top"] < p["threshold"] - p["rearm"]
    return 1 - latch(off, on, start=0.0)


def _score_overlay(f, sf, p):
    risk_off = 1 - latch(sf["top"] >= p["top"], sf["top"] < p["top"] - 10, start=0.0)
    forced_on = hold_after(sf["bottom"] >= p["bottom"], 60)
    return np.maximum(risk_off, forced_on)


def _rsi2(f, sf, p):
    enter = (f["rsi2"] < p["entry"]) & (f["close"] > f["sma200"])
    exit_ = f["close"] > f["sma5"]
    return latch(enter, exit_)


def _zweig(f, sf, p):
    return hold_after(f["zweig_thrust"], p["hold"]) if "zweig_thrust" in f else pd.Series(0.0, index=f.index)


def _vix_spike(f, sf, p):
    from ..quant.technical import expanding_pct

    if "vix" not in f:
        return pd.Series(0.0, index=f.index)
    return hold_after(expanding_pct(f["vix"]) >= p["pct"], p["hold"])


STRATEGIES: dict[str, Strategy] = {
    s.id: s
    for s in [
        Strategy("buy_hold", "Buy and hold", "Always 100% invested (benchmark).", _buy_hold),
        Strategy("trend200", "200-day trend filter", "Long above the 200-day average, cash below (with a +/- band to cut whipsaws).",
                 _trend200, {"band": [0.0, 0.01, 0.02]}, {"band": 0.01}),
        Strategy("bottom_score", "Bottom score entries", "Cash by default; buy when Bottom Opportunity Score >= threshold and hold N sessions.",
                 _bottom_entries, {"threshold": [60, 65, 70, 75, 80], "hold": [20, 60, 126]}, {"threshold": 70, "hold": 60}),
        Strategy("top_overlay", "Top-risk de-risking", "Long by default; go to cash when Top Risk Score >= threshold, back in when it falls by `rearm`.",
                 _top_overlay, {"threshold": [60, 65, 70, 75, 80], "rearm": [10, 20]}, {"threshold": 70, "rearm": 10}),
        Strategy("score_overlay", "Combined score overlay", "Top-risk de-risking, overridden to long for 60 sessions after a high Bottom score.",
                 _score_overlay, {"top": [65, 70, 75, 80], "bottom": [60, 70, 80]}, {"top": 70, "bottom": 70}),
        Strategy("rsi2", "RSI(2) mean reversion", "Connors-style: buy RSI(2) < entry above the 200-day; exit on a close above the 5-day average.",
                 _rsi2, {"entry": [5, 10, 15]}, {"entry": 10}),
        Strategy("zweig", "Zweig breadth thrust", "Buy on a Zweig Breadth Thrust and hold N sessions.", _zweig, {"hold": [63, 126, 252]}, {"hold": 126}, ("breadth",)),
        Strategy("vix_spike", "VIX spike entries", "Buy when VIX is above its historical percentile threshold; hold N sessions.",
                 _vix_spike, {"pct": [0.90, 0.95, 0.98], "hold": [20, 60]}, {"pct": 0.95, "hold": 60}, ("vix",)),
    ]
}


def grid_points(grid: dict) -> list[dict]:
    pts = [{}]
    for k, vals in grid.items():
        pts = [p | {k: v} for p in pts for v in vals]
    return pts
