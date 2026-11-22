"""Data-quality checks. Bad values become NaN and are reported; nothing is interpolated or invented."""

from datetime import date, timedelta

import numpy as np
import pandas as pd


def clean_bars(df: pd.DataFrame, kind: str = "stock") -> tuple[pd.DataFrame, list[str]]:
    issues: list[str] = []
    if df.empty:
        return df, ["no rows returned"]
    df = df[~df.index.duplicated(keep="last")].sort_index().copy()
    bad_close = ~(df["close"] > 0)
    if bad_close.any():
        issues.append(f"{int(bad_close.sum())} rows with missing/non-positive close dropped")
        df = df[~bad_close]
    for c in ("open", "high", "low"):
        bad = df[c].notna() & ~(df[c] > 0)
        if bad.any():
            issues.append(f"{int(bad.sum())} non-positive {c} values set to NaN")
            df.loc[bad, c] = np.nan
    tol = 1.005
    hl_bad = df["high"].notna() & df["low"].notna() & (
        (df["high"] * tol < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1) * tol)
    )
    if hl_bad.any():
        issues.append(f"{int(hl_bad.sum())} rows with inconsistent high/low set to NaN")
        df.loc[hl_bad, ["open", "high", "low"]] = np.nan
    adj = df["adj_close"].where(df["adj_close"] > 0)
    if adj.isna().any():
        df["adj_close"] = adj.fillna(df["close"])
    ret = df["adj_close"].pct_change().abs()
    limit = 0.5 if kind != "index" else 0.35
    jumps = ret > limit
    if jumps.any():  # kept (could be genuine), but surfaced for review
        issues.append(f"{int(jumps.sum())} daily moves > {limit:.0%} (check split adjustment): "
                      + ", ".join(str(d) for d in df.index[jumps][:5]))
    if "volume" in df and (df["volume"] < 0).any():
        df.loc[df["volume"] < 0, "volume"] = np.nan
        issues.append("negative volume set to NaN")
    return df, issues


def is_stale(last: date | None, today: date | None = None, max_age_days: int = 4) -> bool:
    if last is None:
        return True
    return ((today or date.today()) - last) > timedelta(days=max_age_days)
