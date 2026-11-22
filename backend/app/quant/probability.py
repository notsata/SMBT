"""Empirical, out-of-sample-validated conditional frequencies.

Labels use future prices and exist only for evaluation. At date t a prediction may only use labels whose
outcome window closed by t (index <= t - h); that purge is what keeps the walk-forward estimate honest.
"""

import numpy as np
import pandas as pd

HORIZONS = [5, 10, 20, 60, 126, 252]


def fwd_return(close: pd.Series, h: int) -> pd.Series:
    return close.shift(-h) / close - 1


def fwd_min_return(close: pd.Series, h: int) -> pd.Series:
    """Worst close over (t, t+h] relative to close_t; NaN until the whole window exists."""
    fut_min = close[::-1].rolling(h, min_periods=h).min()[::-1].shift(-1)
    return fut_min / close - 1


def episodes(mask: pd.Series, gap: int) -> np.ndarray:
    """Episode id per True day; a new episode starts after more than `gap` sessions without a True day."""
    pos = np.flatnonzero(mask.values)
    if len(pos) == 0:
        return np.array([], dtype=int)
    return np.concatenate([[0], np.cumsum(np.diff(pos) > gap)])


def cluster_bootstrap_ci(values: np.ndarray, ep: np.ndarray, stat=np.mean, n_boot=1000, seed=7) -> tuple[float, float] | None:
    """95% CI resampling whole episodes (overlapping forward windows inside an episode are not independent)."""
    ids = np.unique(ep)
    if len(ids) < 5:
        return None
    rng = np.random.default_rng(seed)
    groups = [values[ep == i] for i in ids]
    stats = [stat(np.concatenate([groups[j] for j in rng.integers(0, len(ids), len(ids))])) for _ in range(n_boot)]
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def describe(values: np.ndarray, ep: np.ndarray | None = None) -> dict:
    v = values[~np.isnan(values)]
    if len(v) == 0:
        return {"n": 0}
    out = {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "median": float(np.median(v)),
        "hit_rate": float((v > 0).mean()),
        "pct": {str(p): float(np.percentile(v, p)) for p in (5, 10, 25, 50, 75, 90, 95)},
    }
    if ep is not None:
        ep = ep[~np.isnan(values)]
        out["episodes"] = int(len(np.unique(ep)))
        ci = cluster_bootstrap_ci(v, ep)
        out["mean_ci95"] = list(ci) if ci else None
        ci = cluster_bootstrap_ci((v > 0).astype(float), ep)
        out["hit_rate_ci95"] = list(ci) if ci else None
    return out


def bucket(score: pd.Series, width: float = 10) -> pd.Series:
    return np.floor(score.clip(0, 99.999) / width)


def conditional(score: pd.Series, outcome: pd.Series, current: float, horizon: int, width: float = 10) -> dict:
    """Outcomes on past days in the same score bucket as `current`, with episode-level CIs."""
    b = bucket(score, width)
    cb = np.floor(min(max(current, 0), 99.999) / width)
    mask = (b == cb) & outcome.notna()
    ep = episodes(mask, horizon)
    vals = outcome[mask].values.astype(float)
    base = describe(outcome.dropna().values.astype(float))
    res = describe(vals, ep)
    res["bucket"] = [cb * width, cb * width + width]
    res["baseline"] = base
    res["sufficient"] = res.get("episodes", 0) >= 8
    return res


def walk_forward_probability(score: pd.Series, label: pd.Series, horizon: int, width: float = 10, min_n: int = 30) -> pd.DataFrame:
    """For each date t: P(label | score bucket) estimated from labels resolved by t, and the base rate."""
    b = bucket(score, width)
    y = label.astype(float)
    nb = int(100 / width)
    resolved_y = y.shift(horizon)  # label of t-h is known at t
    resolved_b = b.shift(horizon)
    ok = resolved_y.notna() & resolved_b.notna()
    cum_n = np.zeros((nb, len(y)))
    cum_y = np.zeros((nb, len(y)))
    for k in range(nb):
        m = ok & (resolved_b == k)
        cum_n[k] = m.cumsum().values
        cum_y[k] = (resolved_y.where(m, 0)).cumsum().values
    tot_n = ok.cumsum().values
    tot_y = resolved_y.where(ok, 0).cumsum().values
    bi = b.fillna(-1).astype(int).values
    idx = np.arange(len(y))
    valid_b = bi >= 0
    n_b = np.where(valid_b, cum_n[np.clip(bi, 0, nb - 1), idx], 0)
    y_b = np.where(valid_b, cum_y[np.clip(bi, 0, nb - 1), idx], 0)
    base = np.where(tot_n > 0, tot_y / np.maximum(tot_n, 1), np.nan)
    p = np.where(n_b >= min_n, y_b / np.maximum(n_b, 1), base)
    out = pd.DataFrame({"p": p, "base": base, "n_bucket": n_b, "label": y.values}, index=score.index)
    out.loc[~valid_b, ["p", "base"]] = np.nan
    return out


def evaluate_oos(wf: pd.DataFrame, start_after: int = 756) -> dict:
    """Brier skill of the bucket model vs the expanding base rate, on dates with a realized label."""
    d = wf.iloc[start_after:].dropna(subset=["p", "base", "label"])
    if len(d) < 100:
        return {"n": int(len(d)), "brier_skill": None, "note": "insufficient out-of-sample history"}
    bs_m = float(((d.p - d.label) ** 2).mean())
    bs_b = float(((d.base - d.label) ** 2).mean())
    cal = []
    for lo in np.arange(0, 1, 0.2):
        m = (d.p >= lo) & (d.p < lo + 0.2 + (1e-9 if lo >= 0.8 else 0))
        if m.sum() >= 20:
            cal.append({"pred": [round(lo, 1), round(lo + 0.2, 1)], "n": int(m.sum()), "mean_pred": float(d.p[m].mean()), "realized": float(d.label[m].mean())})
    return {
        "n": int(len(d)),
        "brier_model": bs_m,
        "brier_base_rate": bs_b,
        "brier_skill": 1 - bs_m / bs_b if bs_b > 0 else None,
        "calibration": cal,
        "note": "positive brier_skill = the score bucket beat the unconditional base rate out of sample",
    }


def score_probabilities(sf: pd.DataFrame, close: pd.Series) -> dict:
    """Headline probability panel for the latest date, every number traceable to counted history."""
    out = {"kind": "empirical", "method": "score-bucket conditional frequency; walk-forward with purged labels"}
    last = sf.index[-1]
    for side, defs in (
        ("bottom", [("positive_return", h, fwd_return(close, h) > 0, fwd_return(close, h)) for h in (20, 60, 126)]),
        ("top", [("correction_10pct", h, fwd_min_return(close, h) <= -0.10, fwd_min_return(close, h)) for h in (60, 126, 252)]),
    ):
        cur = sf[side].iloc[-1]
        res = []
        for name, h, lab, cont in defs:
            lab = lab.where(cont.notna())
            if pd.isna(cur):
                res.append({"event": name, "horizon": h, "available": False})
                continue
            wf = walk_forward_probability(sf[side], lab, h)
            cond = conditional(sf[side], lab.astype(float), cur, h)
            res.append({
                "event": name,
                "horizon": h,
                "available": True,
                "probability": float(wf["p"].iloc[-1]) if pd.notna(wf["p"].iloc[-1]) else None,
                "base_rate": float(wf["base"].iloc[-1]) if pd.notna(wf["base"].iloc[-1]) else None,
                "bucket": cond.get("bucket"),
                "episodes": cond.get("episodes", 0),
                "days": cond.get("n", 0),
                "ci95": cond.get("mean_ci95"),
                "sufficient": cond.get("sufficient", False),
                "oos": evaluate_oos(wf),
                "return_stats": conditional(sf[side], cont, cur, h) if name == "positive_return" else None,
            })
        out[side] = res
    out["as_of"] = last.date().isoformat()
    return out
