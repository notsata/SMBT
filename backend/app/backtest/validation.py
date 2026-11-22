"""Statistical validation: walk-forward optimization, purged time-series CV, event studies with a
random-entry null, and turning-point precision/recall.

Future prices appear only in evaluation labels (forward returns, zigzag pivots); never in signals or in
parameter choices for the period being evaluated.
"""

import numpy as np
import pandas as pd

from ..quant.probability import HORIZONS, describe
from ..quant.technical import expanding_pct, recent
from . import engine
from .strategies import STRATEGIES, Strategy, grid_points

# ---------------------------------------------------------------- walk-forward / CV


def _targets(strat: Strategy, f, sf, grid=None) -> list[tuple[dict, pd.Series]]:
    pts = grid_points(grid if grid is not None else strat.grid) or [strat.defaults]
    return [(p, strat.fn(f, sf, p).reindex(f.index).fillna(0.0)) for p in pts]


def _objective(ret: pd.Series, rf: pd.Series) -> float:
    ex = (ret - rf.reindex(ret.index).fillna(0)).dropna()
    return float(ex.mean() / ex.std() * np.sqrt(252)) if len(ex) > 60 and ex.std() > 0 else -np.inf


def _warmup_start(f, sf) -> pd.Timestamp:
    ok = f["sma200"].notna() & sf["bottom"].notna() & sf["top"].notna()
    return f.index[ok.values.argmax()] if ok.any() else f.index[min(len(f) - 1, 252)]


def missing_inputs(strat: Strategy, f) -> list[str]:
    cols = {"breadth": "zweig_ema", "vix": "vix"}
    return [n for n in strat.needs if cols[n] not in f or not f[cols[n]].notna().any()]


def walk_forward(bars, f, sf, strat_id: str, train_years: int = 6, test_years: int = 2, commission_bps=1.0, slippage_bps=2.0,
                 rf: pd.Series | None = None, extra_benchmarks: dict[str, pd.DataFrame] | None = None) -> dict:
    strat = STRATEGIES[strat_id]
    if missing := missing_inputs(strat, f):
        return {"strategy": strat_id, "error": f"{strat.name} needs {', '.join(missing)} data, which is not loaded."}
    rf_daily = (rf.reindex(bars.index).ffill().fillna(0) / 100 / 252) if rf is not None else pd.Series(0.0, index=bars.index)
    runs = [(p, engine.run(bars, t, commission_bps, slippage_bps, rf)) for p, t in _targets(strat, f, sf)]
    start = _warmup_start(f, sf)
    idx = runs[0][1].index
    idx = idx[idx >= start]
    if len(idx) < (train_years + test_years) * 252:
        return {"strategy": strat_id, "error": f"Need {(train_years + test_years)} years after warm-up; have {len(idx) / 252:.1f}."}
    folds, oos_target = [], pd.Series(np.nan, index=bars.index)
    tr_end = train_years * 252
    while tr_end < len(idx):
        te_end = min(tr_end + test_years * 252, len(idx))
        tr, te = idx[:tr_end], idx[tr_end:te_end]
        scored = []
        for p, res in runs:
            r = res["ret"].loc[tr]
            trades = engine.trade_stats(r, res["weight"].loc[tr]).get("trades", 0)
            ok = strat_id == "buy_hold" or trades >= 3
            scored.append((_objective(r, rf_daily) if ok else -np.inf, p, res))
        best_obj, best_p, best_res = max(scored, key=lambda x: x[0])
        if not np.isfinite(best_obj):
            best_p = strat.defaults
            best_res = next(res for p, res in runs if p == best_p) if any(p == best_p for p, _ in runs) else runs[0][1]
        target = strat.fn(f, sf, best_p).reindex(bars.index).fillna(0.0)
        oos_target.loc[te] = target.loc[te]
        rt = best_res["ret"].loc[te]
        folds.append({
            "train": [tr[0].date().isoformat(), tr[-1].date().isoformat()],
            "test": [te[0].date().isoformat(), te[-1].date().isoformat()],
            "chosen_params": best_p,
            "train_sharpe": None if not np.isfinite(best_obj) else best_obj,
            "test_metrics": engine.metrics(rt, rf_daily, best_res["weight"].loc[te]),
            "test_benchmark": engine.metrics(best_res["asset_ret"].loc[te], rf_daily),
        })
        tr_end = te_end
    oos_idx = idx[train_years * 252 :]
    oos_bars = bars.loc[oos_idx[0] : oos_idx[-1]]
    oos = engine.run(oos_bars, oos_target.loc[oos_bars.index].fillna(0), commission_bps, slippage_bps, rf)
    bench_ret = oos["asset_ret"]
    bull = (f["close"] > f["sma200"]).reindex(oos.index)
    result = {
        "strategy": strat_id,
        "name": strat.name,
        "description": strat.description,
        "folds": folds,
        "oos_period": [oos.index[0].date().isoformat(), oos.index[-1].date().isoformat()],
        "oos": engine.metrics(oos["ret"], oos["rf"], oos["weight"]),
        "oos_benchmark": engine.metrics(bench_ret, oos["rf"]),
        "oos_vs_benchmark": engine.block_bootstrap_diff(oos["ret"], bench_ret),
        "oos_by_market_state": engine.regime_split(oos["ret"], bench_ret, bull),
        "equity": _downsample(oos[["equity", "bench_equity"]]),
        "assumptions": {
            "execution": oos.attrs["execution"],
            "commission_bps": commission_bps,
            "slippage_bps": slippage_bps,
            "cash_yield": "3M T-bill (point-in-time)" if rf is not None and rf.notna().any() else "0% (no T-bill data loaded)",
            "train_years": train_years, "test_years": test_years, "objective": "train-window Sharpe (min 3 trades)",
        },
    }
    for name, eb in (extra_benchmarks or {}).items():
        r, _ = engine.period_returns(eb)
        r = r.reindex(oos.index)
        if r.notna().mean() > 0.95:
            result.setdefault("other_benchmarks", {})[name] = engine.metrics(r.fillna(0), oos["rf"])
    full = engine.run(bars.loc[start:], strat.fn(f, sf, strat.defaults).loc[start:], commission_bps, slippage_bps, rf)
    result["in_sample_defaults"] = {"params": strat.defaults, "metrics": engine.metrics(full["ret"], full["rf"], full["weight"]),
                                    "warning": "Full-period result with fixed default params. Not out-of-sample; do not use as a performance estimate."}
    return result


def purged_cv(bars, f, sf, strat_id: str, k: int = 5, purge: int = 126, embargo: int = 20, commission_bps=1.0, slippage_bps=2.0, rf=None) -> dict:
    """Purged k-fold (contiguous test blocks; training excludes `purge` sessions before and `embargo`
    after each test block). Shows whether parameter choice generalizes across different eras."""
    strat = STRATEGIES[strat_id]
    if missing := missing_inputs(strat, f):
        return {"strategy": strat_id, "error": f"needs {', '.join(missing)} data"}
    rf_daily = (rf.reindex(bars.index).ffill().fillna(0) / 100 / 252) if rf is not None else pd.Series(0.0, index=bars.index)
    runs = [(p, engine.run(bars, t, commission_bps, slippage_bps, rf)) for p, t in _targets(strat, f, sf)]
    idx = runs[0][1].index
    idx = idx[idx >= _warmup_start(f, sf)]
    n = len(idx)
    if n < k * 252:
        return {"strategy": strat_id, "error": "insufficient history for purged CV"}
    bounds = np.linspace(0, n, k + 1).astype(int)
    folds = []
    for i in range(k):
        a, b = bounds[i], bounds[i + 1]
        test = idx[a:b]
        train_pos = np.r_[0 : max(0, a - purge), min(n, b + embargo) : n]
        train = idx[train_pos]
        best = max(runs, key=lambda pr: _objective(pr[1]["ret"].loc[train], rf_daily))
        r = best[1]["ret"].loc[test]
        m, bm = engine.metrics(r, rf_daily, best[1]["weight"].loc[test]), engine.metrics(best[1]["asset_ret"].loc[test], rf_daily)
        folds.append({"test": [test[0].date().isoformat(), test[-1].date().isoformat()], "chosen_params": best[0], "test_metrics": m, "benchmark": bm,
                      "beat_benchmark_sharpe": (m.get("sharpe") or -9) > (bm.get("sharpe") or -9)})
    return {"strategy": strat_id, "k": k, "purge": purge, "embargo": embargo, "folds": folds,
            "share_folds_beating_benchmark": float(np.mean([f_["beat_benchmark_sharpe"] for f_ in folds]))}


def _downsample(df: pd.DataFrame, max_points: int = 600) -> list[dict]:
    step = max(1, len(df) // max_points)
    d = df.iloc[::step]
    if d.index[-1] != df.index[-1]:
        d = pd.concat([d, df.iloc[[-1]]])
    return [{"t": i.date().isoformat(), **{c: float(v) for c, v in r.items()}} for i, r in d.iterrows()]


# ---------------------------------------------------------------- event studies

def signal_library(f: pd.DataFrame, sf: pd.DataFrame, threshold: float = 70) -> dict[str, tuple[str, pd.Series, str]]:
    g = lambda c: f[c].fillna(False).astype(bool) if c in f else pd.Series(False, index=f.index)  # noqa: E731
    lib = {
        "bottom_score": (f"Bottom score >= {threshold:g}", sf["bottom"] >= threshold, "bullish"),
        "top_score": (f"Top risk score >= {threshold:g}", sf["top"] >= threshold, "bearish"),
        "rsi_oversold": ("RSI(14) < 30", f["rsi14"] < 30, "bullish"),
        "rsi_overbought": ("RSI(14) > 75", f["rsi14"] > 75, "bearish"),
        "rsi_bull_div": ("Bullish RSI divergence", g("rsi_bull_div"), "bullish"),
        "rsi_bear_div": ("Bearish RSI divergence", g("rsi_bear_div"), "bearish"),
        "macd_bull_div": ("Bullish MACD divergence", g("macd_bull_div"), "bullish"),
        "macd_bear_div": ("Bearish MACD divergence", g("macd_bear_div"), "bearish"),
        "capitulation_volume": ("Capitulation volume (>=2x avg, >2% down)", (f["vol_ratio"] >= 2) & (f["ret1"] < -0.02), "bullish"),
        "extension": ("Extension: distance from 200d in top 5% of history", expanding_pct(f["dist200"]) >= 0.95, "bearish"),
        "golden_cross": ("50-day crosses above 200-day", (f["sma50"] > f["sma200"]) & (f["sma50"].shift() <= f["sma200"].shift()), "bullish"),
        "death_cross": ("50-day crosses below 200-day", (f["sma50"] < f["sma200"]) & (f["sma50"].shift() >= f["sma200"].shift()), "bearish"),
    }
    if "vix" in f and f["vix"].notna().any():
        lib["vix_spike"] = ("VIX in top 5% of history", expanding_pct(f["vix"]) >= 0.95, "bullish")
    if "vix_term" in f and f["vix_term"].notna().any():
        lib["vix_backwardation"] = ("VIX above VIX3M (backwardation)", f["vix_term"] > 1.0, "bullish")
    if "zweig_thrust" in f and f["zweig_ema"].notna().any():
        lib["zweig_thrust"] = ("Zweig breadth thrust", g("zweig_thrust"), "bullish")
        lib["washout"] = ("90% down day (members)", g("washout_day"), "bullish")
        p50 = f["pct_above_50"]
        lib["breadth_divergence"] = ("Near highs while % above 50d fell 15+ pts in 63d", (f["drawdown"] > -0.03) & ((p50 - p50.shift(63)) <= -15), "bearish")
    return lib


def event_dates(signal: pd.Series, cooldown: int) -> list[int]:
    """Positions of signal rising edges, at least `cooldown` sessions apart."""
    s = signal.fillna(False).astype(bool).values
    edges = np.flatnonzero(s & ~np.r_[False, s[:-1]])
    out, last = [], -10**9
    for i in edges:
        if i - last >= cooldown:
            out.append(int(i))
            last = i
    return out


def _entry_exit(bars: pd.DataFrame, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Per day t: return from next-open entry to close(t+h), and worst low over (t, t+h] vs entry."""
    o = bars["open"].where(bars["open"].notna(), bars["close"]).values
    c = bars["close"].values
    low = bars["low"].where(bars["low"].notna(), bars["close"]).values
    n = len(c)
    ret = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    if n > h:
        entry = o[1 : n - h + 1]
        ret[: n - h] = c[h:] / entry - 1
        lows = pd.Series(low[::-1]).rolling(h, min_periods=h).min().values[::-1]  # min of low[t..t+h-1]
        mae[: n - h] = lows[1 : n - h + 1] / entry - 1
    return ret, mae


def event_study(bars: pd.DataFrame, signal: pd.Series, direction: str = "bullish", cooldown: int = 20, horizons=HORIZONS,
                n_random: int = 2000, warmup: int = 252, seed: int = 3) -> dict:
    signal = signal.reindex(bars.index).fillna(False)
    ev = [i for i in event_dates(signal, cooldown) if i >= warmup]
    rng = np.random.default_rng(seed)
    out = {"n_events": len(ev), "cooldown": cooldown, "direction": direction, "events": [bars.index[i].date().isoformat() for i in ev], "horizons": {}}
    for h in horizons:
        ret, mae = _entry_exit(bars, h)
        eligible = np.arange(warmup, len(bars) - h)
        evh = [i for i in ev if i < len(bars) - h]
        base = ret[eligible]
        st = describe(ret[evh]) if evh else {"n": 0}
        st["baseline"] = describe(base)
        if len(evh) >= 5:
            obs = np.nanmean(ret[evh])
            sims = np.array([np.nanmean(base[rng.integers(0, len(base), len(evh))]) for _ in range(n_random)])
            # One-sided: bullish signals should beat random entries, bearish signals should underperform them.
            st["p_value_vs_random"] = float((sims >= obs).mean() if direction == "bullish" else (sims <= obs).mean())
            st["excess_mean"] = float(obs - np.nanmean(base))
            st["excess_median"] = float(np.nanmedian(ret[evh]) - np.nanmedian(base))
            st["mae_median"] = float(np.nanmedian(mae[evh]))
        st["significant_5pct"] = bool(st.get("p_value_vs_random", 1) < 0.05)
        out["horizons"][str(h)] = st
    return out


# ---------------------------------------------------------------- turning points

def zigzag(close: pd.Series, pct: float = 0.10) -> list[dict]:
    """Ex-post swing pivots: a peak/trough is confirmed once price reverses by `pct`. Evaluation only."""
    c = close.dropna()
    v = c.values
    piv, trend, hi, lo = [], 0, 0, 0
    add = lambda i, k: piv.append({"date": c.index[i], "price": float(v[i]), "kind": k})  # noqa: E731
    for i in range(1, len(v)):
        if trend == 0:
            hi = i if v[i] > v[hi] else hi
            lo = i if v[i] < v[lo] else lo
            if v[i] >= v[lo] * (1 + pct):
                add(lo, "trough"); trend, hi = 1, i  # noqa: E702
            elif v[i] <= v[hi] * (1 - pct):
                add(hi, "peak"); trend, lo = -1, i  # noqa: E702
        elif trend == 1:
            if v[i] > v[hi]:
                hi = i
            elif v[i] <= v[hi] * (1 - pct):
                add(hi, "peak"); trend, lo = -1, i  # noqa: E702
        else:
            if v[i] < v[lo]:
                lo = i
            elif v[i] >= v[lo] * (1 + pct):
                add(lo, "trough"); trend, hi = 1, i  # noqa: E702
    return piv


def turning_points(bars: pd.DataFrame, signal: pd.Series, direction: str, pct: float = 0.10, window: int = 20, cooldown: int = 20) -> dict:
    """Precision: share of signals within +/- window sessions of a significant pivot of the right kind.
    Recall: share of those pivots that had a signal within the window."""
    kind = "trough" if direction == "bullish" else "peak"
    piv = [p for p in zigzag(bars["close"], pct) if p["kind"] == kind]
    pos = {d: i for i, d in enumerate(bars.index)}
    pp = np.array([pos[p["date"]] for p in piv], dtype=int)
    ev = np.array(event_dates(signal.reindex(bars.index).fillna(False), cooldown), dtype=int)
    if len(ev) == 0 or len(pp) == 0:
        return {"pivots": len(pp), "signals": len(ev), "precision": None, "recall": None, "pivot_threshold": pct, "window": window}
    dist = ev[:, None] - pp[None, :]
    near = np.abs(dist) <= window
    hit_sig = near.any(axis=1)
    hit_piv = near.any(axis=0)
    lags = [int(dist[i][near[i]][np.argmin(np.abs(dist[i][near[i]]))]) for i in np.flatnonzero(hit_sig)]
    return {
        "pivot_threshold": pct, "window": window, "pivots": int(len(pp)), "signals": int(len(ev)),
        "precision": float(hit_sig.mean()), "recall": float(hit_piv.mean()),
        "false_positives": int((~hit_sig).sum()), "missed_pivots": int((~hit_piv).sum()),
        "median_lag_sessions": float(np.median(lags)) if lags else None,
        "missed": [bars.index[p].date().isoformat() for p in pp[~hit_piv]][:30],
        "pivot_dates": [bars.index[p].date().isoformat() for p in pp],
        "note": "Pivots are defined after the fact (zigzag) and used only to grade signals, never to create them.",
    }


def cv_signal_stability(bars: pd.DataFrame, signal: pd.Series, direction: str, k: int = 5, horizon: int = 60, cooldown: int = 20) -> list[dict]:
    """Event-study excess return per contiguous era: is the edge stable or one lucky period?"""
    n = len(bars)
    bounds = np.linspace(252, n, k + 1).astype(int)
    out = []
    for i in range(k):
        a, b = bounds[i], bounds[i + 1]
        sub = bars.iloc[max(0, a - 252) : b]
        es = event_study(sub, signal.reindex(sub.index), direction, cooldown, [horizon], n_random=500)
        h = es["horizons"][str(horizon)]
        out.append({"period": [bars.index[a].date().isoformat(), bars.index[b - 1].date().isoformat()], "n_events": h.get("n", 0),
                    "excess_mean": h.get("excess_mean"), "hit_rate": h.get("hit_rate"), "baseline_hit_rate": h.get("baseline", {}).get("hit_rate")})
    return out


def signal_report(bars, f, sf, name: str, threshold: float = 70, cooldown: int = 20, pivot_pct: float = 0.10) -> dict:
    lib = signal_library(f, sf, threshold)
    if name not in lib:
        raise KeyError(name)
    label, sig, direction = lib[name]
    return {
        "signal": name, "label": label, "direction": direction,
        "event_study": event_study(bars, sig, direction, cooldown),
        "turning_points": turning_points(bars, sig, direction, pivot_pct, cooldown=cooldown),
        "stability_by_era": cv_signal_stability(bars, sig, direction, cooldown=cooldown),
        "method": "Entry at next session's open after the signal close; exit at close h sessions later. p-value: share of "
                  "2000 random same-size entry sets that did at least as well (one-sided). Events de-clustered by cooldown.",
    }


def signal_scoreboard(bars, f, sf, horizon: int = 60) -> list[dict]:
    rows = []
    for name, (label, sig, direction) in signal_library(f, sf).items():
        es = event_study(bars, sig, direction, 20, [horizon], n_random=1000)
        h = es["horizons"][str(horizon)]
        rows.append({"signal": name, "label": label, "direction": direction, "n_events": h.get("n", 0), "median": h.get("median"),
                     "hit_rate": h.get("hit_rate"), "baseline_hit_rate": h.get("baseline", {}).get("hit_rate"),
                     "excess_mean": h.get("excess_mean"), "p_value": h.get("p_value_vs_random"), "significant": h.get("significant_5pct", False),
                     "active_now": bool(recent(sig.fillna(False).astype(bool), 5).iloc[-1])})
    q = bh_qvalues([r["p_value"] for r in rows])
    for r, qv in zip(rows, q):
        r["q_value"] = qv
        r["significant_after_fdr"] = qv is not None and qv < 0.10
    return sorted(rows, key=lambda r: (r["p_value"] is None, r["p_value"] if r["p_value"] is not None else 1))


def bh_qvalues(p: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg adjusted p-values: testing many signals at once inflates raw significance."""
    idx = [i for i, v in enumerate(p) if v is not None]
    if not idx:
        return [None] * len(p)
    vals = np.array([p[i] for i in idx])
    order = np.argsort(vals)
    m = len(vals)
    adj = vals[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1].clip(0, 1)
    out: list[float | None] = [None] * len(p)
    for rank, j in enumerate(order):
        out[idx[j]] = float(adj[rank])
    return out
