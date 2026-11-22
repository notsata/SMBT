"""Market breadth from a constituent close matrix (dates x symbols).

Uses S&P 500 members, not the full NYSE tape (no free official source for exchange-wide A/D). With a
membership mask, only symbols that were members on each date count (survivorship control); without one
the history is computed over today's members and is survivorship-biased, which callers must disclose.
"""

import numpy as np
import pandas as pd

from .technical import rsi


def breadth_frame(close: pd.DataFrame, member_mask: pd.DataFrame | None = None, min_names: int = 100) -> pd.DataFrame:
    close = close.sort_index()
    valid = close.notna()
    if member_mask is not None:
        valid &= member_mask.reindex(index=close.index, columns=close.columns, fill_value=False)
    ret = close.pct_change(fill_method=None)
    rv = valid & ret.notna()
    adv = ((ret > 0) & rv).sum(axis=1)
    dec = ((ret < 0) & rv).sum(axis=1)
    n = rv.sum(axis=1)
    out = pd.DataFrame(index=close.index)
    out["breadth_names"] = n
    out["advances"], out["declines"] = adv, dec
    tot = (adv + dec).replace(0, np.nan)
    rana = (adv - dec) / tot * 1000
    out["ad_line"] = (adv - dec).cumsum()
    out["mcclellan_osc"] = rana.ewm(alpha=0.10, adjust=False).mean() - rana.ewm(alpha=0.05, adjust=False).mean()
    out["mcclellan_sum"] = out["mcclellan_osc"].cumsum()
    for w in (20, 50, 200):
        sma = close.rolling(w, min_periods=w).mean()
        ok = valid & sma.notna()
        out[f"pct_above_{w}"] = ((close > sma) & ok).sum(axis=1) / ok.sum(axis=1).replace(0, np.nan) * 100
    hi = close.rolling(252, min_periods=252).max()
    lo = close.rolling(252, min_periods=252).min()
    ok = valid & hi.notna()
    nh, nl = ((close >= hi) & ok).sum(axis=1), ((close <= lo) & ok).sum(axis=1)
    denom = ok.sum(axis=1).replace(0, np.nan)
    out["new_highs"], out["new_lows"] = nh, nl
    out["nhnl_pct"] = (nh - nl) / denom * 100
    r = rsi(close, 14)
    okr = valid & r.notna()
    out["pct_oversold"] = ((r < 30) & okr).sum(axis=1) / okr.sum(axis=1).replace(0, np.nan) * 100
    out["pct_overbought"] = ((r > 70) & okr).sum(axis=1) / okr.sum(axis=1).replace(0, np.nan) * 100
    # Zweig Breadth Thrust: 10-day EMA of A/(A+D) rises from < 0.40 to > 0.615 within 10 sessions.
    zb = (adv / tot).ewm(span=10, adjust=False).mean()
    out["zweig_ema"] = zb
    thrust = (zb > 0.615) & (zb.rolling(11, min_periods=11).min() < 0.40)
    out["zweig_thrust"] = thrust & ~thrust.shift(1, fill_value=False)
    # Washout: >= 90% of decliners (a classic capitulation "90% down day" proxy on members, not NYSE volume).
    out["washout_day"] = (dec / tot) >= 0.90
    thin = n < min_names
    flags = ["zweig_thrust", "washout_day"]
    out[flags] = out[flags].fillna(False).astype(bool)
    out.loc[thin, flags] = False
    cols = [c for c in out.columns if c not in ("breadth_names", "advances", "declines", *flags)]
    out.loc[thin, cols] = np.nan
    return out
