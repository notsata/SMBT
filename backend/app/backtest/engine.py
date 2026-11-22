"""Vectorized daily backtester.

Timing contract: a target weight decided from data at close t is executed at the OPEN of t+1 and earns the
open-to-open return from t+1 onward. Without open prices it executes at the close of t+1 (a full day of
delay, conservative). Costs (commission + slippage, bps per side) are charged on every weight change.
Idle cash earns the point-in-time T-bill rate when available.
"""

import numpy as np
import pandas as pd

ANN = 252


def period_returns(bars: pd.DataFrame) -> tuple[pd.Series, str]:
    o = bars["open"]
    if o.notna().mean() > 0.98:
        o = o.fillna(bars["close"])
        return (o.shift(-1) / o - 1), "next_open"
    c = bars["close"]
    return (c.shift(-1) / c - 1), "next_close"


def run(bars: pd.DataFrame, target: pd.Series, commission_bps: float = 1.0, slippage_bps: float = 2.0, rf_annual_pct: pd.Series | None = None) -> pd.DataFrame:
    r, mode = period_returns(bars)
    tgt = target.reindex(bars.index).astype(float).clip(0, 1)
    # Weight decided at close t-1 is filled at open t (or close t) and earns r[t]; never the same bar's return.
    w = tgt.shift(1).fillna(0.0)
    rf = (rf_annual_pct.reindex(bars.index).ffill().fillna(0) / 100 / ANN) if rf_annual_pct is not None else pd.Series(0.0, index=bars.index)
    turnover = w.diff().abs().fillna(w.abs())
    cost = turnover * (commission_bps + slippage_bps) / 1e4
    strat = w * r + (1 - w) * rf - cost
    out = pd.DataFrame({"weight": w, "asset_ret": r, "rf": rf, "cost": cost, "ret": strat})
    out = out.iloc[:-1]  # last row has no forward return yet
    out["equity"] = (1 + out["ret"]).cumprod()
    out["bench_equity"] = (1 + out["asset_ret"].fillna(0)).cumprod()
    out.attrs["execution"] = mode
    return out


def max_drawdown(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min()) if len(eq) else float("nan")


def metrics(ret: pd.Series, rf: pd.Series | None = None, weight: pd.Series | None = None) -> dict:
    ret = ret.dropna()
    if len(ret) < 20:
        return {"n_days": int(len(ret))}
    rf = (rf.reindex(ret.index).fillna(0) if rf is not None else pd.Series(0.0, index=ret.index))
    ex = ret - rf
    eq = (1 + ret).cumprod()
    yrs = len(ret) / ANN
    downside = ex[ex < 0].std() * np.sqrt(ANN)
    vol = ret.std() * np.sqrt(ANN)
    cagr = float(eq.iloc[-1] ** (1 / yrs) - 1) if eq.iloc[-1] > 0 else -1.0
    mdd = max_drawdown(eq)
    out = {
        "n_days": int(len(ret)),
        "years": round(yrs, 2),
        "total_return": float(eq.iloc[-1] - 1),
        "cagr": cagr,
        "volatility": float(vol),
        "sharpe": float(ex.mean() / ex.std() * np.sqrt(ANN)) if ex.std() > 0 else None,
        "sortino": float(ex.mean() * ANN / downside) if downside > 0 else None,
        "max_drawdown": mdd,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else None,
    }
    if weight is not None:
        w = weight.reindex(ret.index).fillna(0)
        out["exposure"] = float(w.mean())
        out |= trade_stats(ret, w)
    return out


def trade_stats(ret: pd.Series, w: pd.Series) -> dict:
    """Round trips: contiguous runs of non-zero weight."""
    on = (w > 0).astype(int)
    start = (on.diff().fillna(on) == 1)
    tid = start.cumsum().where(on == 1)
    if tid.dropna().empty:
        return {"trades": 0}
    tr = (1 + ret).groupby(tid).prod() - 1
    wins, losses = tr[tr > 0], tr[tr <= 0]
    return {
        "trades": int(len(tr)),
        "win_rate": float((tr > 0).mean()),
        "avg_trade": float(tr.mean()),
        "median_trade": float(tr.median()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "avg_days_in_trade": float(on.sum() / len(tr)),
    }


def block_bootstrap_diff(a: pd.Series, b: pd.Series, block: int = 20, n_boot: int = 1000, seed: int = 11) -> dict:
    """Paired moving-block bootstrap CI for (Sharpe_a - Sharpe_b) and annualized mean return difference.
    Blocks preserve autocorrelation and volatility clustering."""
    df = pd.concat([a, b], axis=1).dropna()
    if len(df) < block * 10:
        return {}
    x, y = df.iloc[:, 0].values, df.iloc[:, 1].values
    n = len(x)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    sh = lambda v: v.mean() / v.std() * np.sqrt(ANN) if v.std() > 0 else 0.0  # noqa: E731
    ds, dm = [], []
    for _ in range(n_boot):
        starts = rng.integers(0, n - block + 1, nb)
        idx = (starts[:, None] + np.arange(block)).ravel()[:n]
        xs, ys = x[idx], y[idx]
        ds.append(sh(xs) - sh(ys))
        dm.append((xs.mean() - ys.mean()) * ANN)
    ds, dm = np.array(ds), np.array(dm)
    return {
        "sharpe_diff": float(sh(x) - sh(y)),
        "sharpe_diff_ci95": [float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))],
        "p_sharpe_diff_le_0": float((ds <= 0).mean()),
        "ann_return_diff": float((x.mean() - y.mean()) * ANN),
        "ann_return_diff_ci95": [float(np.percentile(dm, 2.5)), float(np.percentile(dm, 97.5))],
        "block": block,
        "n_boot": n_boot,
    }


def regime_split(ret: pd.Series, bench_ret: pd.Series, bull: pd.Series) -> dict:
    """Performance split by market state (bull = benchmark above its 200-day average, known at t)."""
    out = {}
    for name, m in (("bull", bull), ("bear", ~bull)):
        m = m.reindex(ret.index).fillna(False).astype(bool)
        if m.sum() < 60:
            out[name] = {"n_days": int(m.sum())}
            continue
        r, br = ret[m], bench_ret.reindex(ret.index)[m].fillna(0)
        out[name] = {"n_days": int(m.sum()), "ann_return": float(r.mean() * ANN), "bench_ann_return": float(br.mean() * ANN),
                     "sharpe": float(r.mean() / r.std() * np.sqrt(ANN)) if r.std() > 0 else None,
                     "bench_sharpe": float(br.mean() / br.std() * np.sqrt(ANN)) if br.std() > 0 else None}
    return out
