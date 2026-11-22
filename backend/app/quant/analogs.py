"""Historical analog engine: nearest past dates by a point-in-time feature fingerprint, and what followed.

Similarity is descriptive (how alike conditions look), not a forecast. Outcomes are realized forward
returns from those dates; a match whose forward window has not closed reports null for that horizon.
"""

import numpy as np
import pandas as pd

from .probability import HORIZONS, describe, fwd_min_return, fwd_return
from .technical import expanding_pct

FEATURES = {
    "momentum": ["dist200", "roc63"],
    "drawdown": ["drawdown"],
    "breadth": ["pct_above_50", "nhnl_pct"],
    "sentiment": ["vix", "vix_term", "fear_greed"],
    "volatility": ["hv20"],
    "valuation": ["mcap_gdp"],
    "credit": ["credit_spread", "credit_chg63"],
    "liquidity": ["net_liq_chg63"],
    "rates": ["curve_10y2y", "real_10y"],
}

EVENTS = [
    ("Dot-com peak", "2000-01-14", "2000-09-01"),
    ("Dot-com bear market", "2000-09-02", "2002-06-30"),
    ("2002 bear-market bottom", "2002-07-01", "2003-03-31"),
    ("Pre-GFC peak", "2007-06-01", "2007-12-31"),
    ("Financial crisis crash", "2008-01-01", "2009-02-28"),
    ("GFC bottom / early recovery", "2009-03-01", "2009-06-30"),
    ("2010 flash-crash correction", "2010-04-23", "2010-07-02"),
    ("2011 debt-ceiling correction", "2011-07-22", "2011-10-31"),
    ("2015-16 China/oil correction", "2015-08-01", "2016-02-29"),
    ("Feb 2018 volatility shock", "2018-01-26", "2018-04-30"),
    ("Q4 2018 selloff", "2018-09-20", "2018-12-31"),
    ("Pre-COVID peak", "2020-01-15", "2020-02-21"),
    ("COVID crash", "2020-02-22", "2020-04-30"),
    ("2022 inflation bear market", "2022-01-03", "2022-10-31"),
    ("2023 regional bank stress", "2023-03-08", "2023-03-31"),
    ("Aug 2024 yen carry unwind", "2024-07-16", "2024-08-15"),
    ("2025 tariff shock", "2025-02-19", "2025-04-30"),
]

KEY_DATES = [
    ("2000-03-24", "Dot-com peak", "top"),
    ("2002-10-09", "2002 bear-market low", "bottom"),
    ("2007-10-09", "2007 pre-GFC peak", "top"),
    ("2009-03-09", "GFC low", "bottom"),
    ("2011-10-03", "2011 correction low", "bottom"),
    ("2016-02-11", "2016 correction low", "bottom"),
    ("2018-09-20", "2018 peak", "top"),
    ("2018-12-24", "2018 low", "bottom"),
    ("2020-02-19", "Pre-COVID peak", "top"),
    ("2020-03-23", "COVID low", "bottom"),
    ("2022-01-03", "2022 peak", "top"),
    ("2022-10-12", "2022 bear-market low", "bottom"),
    ("2025-04-08", "2025 tariff-shock low", "bottom"),
]


def event_label(d: pd.Timestamp) -> str | None:
    for name, a, b in EVENTS:
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return name
    return None


def fingerprint(f: pd.DataFrame) -> pd.DataFrame:
    """Each feature as its point-in-time percentile (0..1) so different units are comparable."""
    cols = {}
    for group, names in FEATURES.items():
        for n in names:
            if n in f and f[n].notna().sum() > 300:
                cols[f"{group}:{n}"] = expanding_pct(f[n].astype(float), 252)
    return pd.DataFrame(cols, index=f.index)


def _outcomes(close: pd.Series, i: int) -> dict:
    out = {}
    for h in HORIZONS:
        j = i + h
        out[str(h)] = float(close.iloc[j] / close.iloc[i] - 1) if j < len(close) else None
    mdd = fwd_min_return(close, 126).iloc[i]
    out["max_drawdown_126"] = None if pd.isna(mdd) else float(mdd)
    return out


def find_analogs(f: pd.DataFrame, k: int = 10, exclude_recent: int = 126, decluster: int = 40, min_features: int = 4) -> dict:
    X = fingerprint(f)
    if X.empty:
        return {"kind": "historical_similarity", "matches": [], "note": "no features available"}
    x0 = X.iloc[-1]
    used = x0.dropna().index
    if len(used) < min_features:
        return {"kind": "historical_similarity", "matches": [], "note": f"only {len(used)} features available today"}
    cand = X[used].iloc[: max(0, len(X) - exclude_recent)]
    common = cand.notna()
    need = max(min_features, int(np.ceil(0.6 * len(used))))
    d2 = ((cand - x0[used]) ** 2).where(common).sum(axis=1) / common.sum(axis=1).replace(0, np.nan)
    sim = (1 - np.sqrt(d2)).where(common.sum(axis=1) >= need).dropna().sort_values(ascending=False)
    close = f["close"]
    picks: list[pd.Timestamp] = []
    pos = {d: i for i, d in enumerate(f.index)}
    for d in sim.index:
        if all(abs(pos[d] - pos[p]) > decluster for p in picks):
            picks.append(d)
            if len(picks) == k:
                break
    matches = []
    for d in picks:
        i = pos[d]
        feats = {c: {"then": round(float(X.at[d, c]), 3) if pd.notna(X.at[d, c]) else None, "now": round(float(x0[c]), 3)} for c in used}
        matches.append({"date": d.date().isoformat(), "similarity": round(float(sim[d]), 3), "event": event_label(d), "features": feats, "forward": _outcomes(close, i)})
    dist = {}
    for h in HORIZONS:
        vals = np.array([m["forward"][str(h)] for m in matches if m["forward"][str(h)] is not None], dtype=float)
        dist[str(h)] = describe(vals) if len(vals) else {"n": 0}
    uncond = {str(h): describe(fwd_return(close, h).dropna().values) for h in HORIZONS}
    return {
        "kind": "historical_similarity",
        "as_of": f.index[-1].date().isoformat(),
        "features_used": list(used),
        "matches": matches,
        "outcome_distribution": dist,
        "unconditional": uncond,
        "note": "Similarity of point-in-time percentile fingerprints (1 = identical). Matches are de-clustered "
                f"(>= {decluster} sessions apart) and exclude the last {exclude_recent} sessions. Small samples: read as context, not odds.",
    }


def compare_key_dates(f: pd.DataFrame) -> list[dict]:
    X = fingerprint(f)
    if X.empty:
        return []
    x0 = X.iloc[-1]
    out = []
    for ds, name, kind in KEY_DATES:
        d = pd.Timestamp(ds)
        if d not in X.index:
            near = X.index[X.index >= d]
            if len(near) == 0 or (near[0] - d).days > 5:
                continue
            d = near[0]
        if X.index.get_loc(d) > len(X) - 21:  # comparing today with last week says nothing
            continue
        row = X.loc[d]
        common = row.notna() & x0.notna()
        if common.sum() < 3:
            continue
        sim = 1 - float(np.sqrt(((row[common] - x0[common]) ** 2).mean()))
        out.append({"date": d.date().isoformat(), "name": name, "kind": kind, "similarity": round(sim, 3), "features_compared": int(common.sum()),
                    "forward": _outcomes(f["close"], f.index.get_loc(d))})
    return sorted(out, key=lambda r: -r["similarity"])
