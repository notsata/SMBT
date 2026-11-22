"""Causal technical indicators. Every value at row t uses only rows <= t (verified by truncation tests).

The one exception is pivot detection, which needs k bars after a pivot; such signals are emitted on the
confirmation bar (pivot + k), never on the pivot bar itself.
"""

import numpy as np
import pandas as pd

ANN = np.sqrt(252)


def wilder(x: pd.Series | pd.DataFrame, n: int):
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series | pd.DataFrame, n: int = 14):
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder(-d.clip(upper=0), n)
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast=12, slow=26, sig=9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False, min_periods=fast).mean() - close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    signal = line.ewm(span=sig, adjust=False, min_periods=sig).mean()
    return pd.DataFrame({"macd": line, "macd_signal": signal, "macd_hist": line - signal})


def stoch_rsi(close: pd.Series, n=14, k=3, d=3) -> pd.DataFrame:
    r = rsi(close, n)
    lo, hi = r.rolling(n).min(), r.rolling(n).max()
    raw = ((r - lo) / (hi - lo).replace(0, np.nan)) * 100
    kk = raw.rolling(k).mean()
    return pd.DataFrame({"stochrsi_k": kk, "stochrsi_d": kk.rolling(d).mean()})


def bollinger(close: pd.Series, n=20, m=2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    up, lo = mid + m * sd, mid - m * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": up, "bb_lower": lo, "bb_pctb": (close - lo) / (up - lo), "bb_width": (up - lo) / mid})


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1, skipna=False)
    return tr.fillna((df["close"] - pc).abs())  # close-only series (e.g. VVIX) fall back to |dClose|


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n=14) -> pd.DataFrame:
    h, l = df["high"].fillna(df["close"]), df["low"].fillna(df["close"])
    upm, dnm = h.diff(), -l.diff()
    pdm = upm.where((upm > dnm) & (upm > 0), 0.0)
    ndm = dnm.where((dnm > upm) & (dnm > 0), 0.0)
    a = atr(df, n).replace(0, np.nan)
    pdi, ndi = 100 * wilder(pdm, n) / a, 100 * wilder(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": pdi, "minus_di": ndi})


def hist_vol(close: pd.Series, n=20) -> pd.Series:
    return np.log(close).diff().rolling(n).std() * ANN


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"].fillna(0)).cumsum()


def anchored_vwap(df: pd.DataFrame, anchor) -> pd.Series:
    sub = df.loc[anchor:]
    tp = (sub["high"].fillna(sub["close"]) + sub["low"].fillna(sub["close"]) + sub["close"]) / 3
    v = sub["volume"].fillna(0)
    return ((tp * v).cumsum() / v.cumsum().replace(0, np.nan)).reindex(df.index)


def volume_profile(df: pd.DataFrame, lookback=126, bins=24) -> dict | None:
    sub = df.tail(lookback).dropna(subset=["close"])
    if sub["volume"].fillna(0).sum() <= 0 or len(sub) < 20:
        return None
    tp = (sub["high"].fillna(sub["close"]) + sub["low"].fillna(sub["close"]) + sub["close"]) / 3
    hist, edges = np.histogram(tp, bins=bins, weights=sub["volume"].fillna(0))
    order = np.argsort(hist)[::-1]
    cum, va = 0.0, []
    for i in order:  # value area: highest-volume bins until 70% of volume
        va.append(i)
        cum += hist[i]
        if cum >= 0.7 * hist.sum():
            break
    poc = int(order[0])
    return {
        "bins": [{"lo": float(edges[i]), "hi": float(edges[i + 1]), "volume": float(hist[i])} for i in range(bins)],
        "poc": float((edges[poc] + edges[poc + 1]) / 2),
        "value_area_low": float(edges[min(va)]),
        "value_area_high": float(edges[max(va) + 1]),
        "lookback": lookback,
    }


def expanding_pct(x: pd.Series, min_periods=252) -> pd.Series:
    """Percentile of today's value within its own history up to today (0..1). No future data."""
    return x.expanding(min_periods=min_periods).rank(pct=True)


def pivots(x: pd.Series, k: int, kind: str) -> pd.Series:
    """Boolean at the pivot bar. Callers must shift by k to the confirmation bar."""
    roll = x.rolling(2 * k + 1, center=True, min_periods=2 * k + 1)
    ext = roll.min() if kind == "low" else roll.max()
    return (x == ext) & ext.notna()


def divergences(price: pd.Series, osc: pd.Series, k=5, max_gap=60, min_gap=8, lo_th=40.0, hi_th=60.0) -> tuple[pd.Series, pd.Series, list[dict]]:
    """Regular divergences between price and an oscillator using confirmed pivots.
    Bullish: price lower low, oscillator higher low, first low oscillator < lo_th.
    Bearish: price higher high, oscillator lower high, first high oscillator > hi_th.
    Returns bool Series (bull, bear) on the confirmation bar and event details."""
    idx = price.index
    bull = pd.Series(False, index=idx)
    bear = pd.Series(False, index=idx)
    events = []
    p, o = price.values, osc.values
    for kind, out in (("low", bull), ("high", bear)):
        piv = np.flatnonzero(pivots(price, k, kind).values)
        for a, b in zip(piv[:-1], piv[1:]):
            if not (min_gap <= b - a <= max_gap) or b + k >= len(idx) or np.isnan(o[a]) or np.isnan(o[b]):
                continue
            if kind == "low" and p[b] < p[a] and o[b] > o[a] and o[a] < lo_th:
                ok = True
            elif kind == "high" and p[b] > p[a] and o[b] < o[a] and o[a] > hi_th:
                ok = True
            else:
                ok = False
            if ok:
                out.iloc[b + k] = True
                events.append({
                    "type": "bullish" if kind == "low" else "bearish",
                    "confirmed": idx[b + k].date().isoformat(),
                    "pivot1": idx[a].date().isoformat(),
                    "pivot2": idx[b].date().isoformat(),
                    "price": [float(p[a]), float(p[b])],
                    "osc": [float(o[a]), float(o[b])],
                })
    return bull, bear, events


def recent(flag: pd.Series, n: int) -> pd.Series:
    """True if flag fired in the last n bars (inclusive)."""
    return flag.astype(float).rolling(n, min_periods=1).max().astype(bool)


def technical_frame(df: pd.DataFrame, bench_close: pd.Series | None = None) -> pd.DataFrame:
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    for n in (20, 50, 100, 200):
        out[f"sma{n}"] = c.rolling(n).mean()
    out["dist200"] = c / out["sma200"] - 1
    out["dist50"] = c / out["sma50"] - 1
    out["sma200_slope"] = out["sma200"].pct_change(20)
    out["rsi14"] = rsi(c, 14)
    out["rsi2"] = rsi(c, 2)
    out = out.join(macd(c)).join(stoch_rsi(c)).join(bollinger(c)).join(adx(df))
    out["atr14"] = atr(df)
    out["atr_pct"] = out["atr14"] / c
    out["ext_atr"] = (c - out["sma50"]) / out["atr14"]
    out["hv20"] = hist_vol(c, 20)
    out["ret1"] = c.pct_change()
    out["roc20"] = c.pct_change(20)
    out["roc63"] = c.pct_change(63)
    out["roc126"] = c.pct_change(126)
    out["hi252"] = c.rolling(252, min_periods=50).max()
    out["lo252"] = c.rolling(252, min_periods=50).min()
    out["drawdown"] = c / out["hi252"] - 1
    out["sma5"] = c.rolling(5).mean()
    vol = df["volume"]
    has_vol = vol.fillna(0).gt(0).rolling(50).sum() >= 40
    out["vol_ratio"] = (vol / vol.rolling(50).mean()).where(has_vol)
    out["obv"] = obv(df).where(has_vol)
    out["obv_slope20"] = (out["obv"].diff(20) / vol.rolling(20).sum()).where(has_vol)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    out["close_loc"] = (c - df["low"]) / rng  # 0 = closed on the low
    bull, bear, _ = divergences(c, out["rsi14"])
    out["rsi_bull_div"], out["rsi_bear_div"] = bull, bear
    mb, mbe, _ = divergences(c, out["macd_hist"], lo_th=0.0, hi_th=0.0)
    out["macd_bull_div"], out["macd_bear_div"] = mb, mbe
    # Volume/price divergence: price up over 20d but OBV down (bearish), or the reverse.
    out["vp_bear_div"] = (out["roc20"] > 0.03) & (out["obv_slope20"] < -0.05)
    out["vp_bull_div"] = (out["roc20"] < -0.03) & (out["obv_slope20"] > 0.05)
    # Trend quality: R^2 of 63-day log-price regression.
    lc = np.log(c)
    t = pd.Series(np.arange(len(c)), index=c.index, dtype=float)
    out["r2_63"] = (lc.rolling(63).corr(t) ** 2)
    if bench_close is not None:
        b = bench_close.reindex(c.index)
        ratio = c / b
        out["rs_ratio"] = ratio
        out["rs63"] = ratio.pct_change(63)
        out["rs126"] = ratio.pct_change(126)
    return out
