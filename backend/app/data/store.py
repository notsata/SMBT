"""Read-side helpers: DB rows -> analysis-ready pandas objects (split/dividend adjusted, point-in-time)."""

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Bar, Instrument, Membership, SeriesObs


def load_bars(s: Session, symbol: str) -> pd.DataFrame:
    rows = s.execute(
        select(Bar.date, Bar.open, Bar.high, Bar.low, Bar.close, Bar.adj_close, Bar.volume, Bar.source, Bar.fetched_at)
        .where(Bar.symbol == symbol)
        .order_by(Bar.date)
    ).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "raw_close", "adj_close", "volume", "source", "fetched_at"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date")), name="date")
    f = (df["adj_close"] / df["raw_close"]).where(lambda x: x > 0, 1.0)
    out = pd.DataFrame(
        {
            "open": df["open"] * f,
            "high": df["high"] * f,
            "low": df["low"] * f,
            "close": df["adj_close"].fillna(df["raw_close"]),
            "volume": df["volume"],
        }
    ).astype(float)
    out.attrs = {
        "symbol": symbol,
        "source": df["source"].iloc[-1],
        "fetched_at": str(df["fetched_at"].iloc[-1]),
        "last_date": df.index[-1].date().isoformat(),
    }
    return out


def load_close_matrix(s: Session, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    frames = []
    for i in range(0, len(symbols), 400):
        rows = s.execute(
            select(Bar.symbol, Bar.date, func.coalesce(Bar.adj_close, Bar.close)).where(Bar.symbol.in_(symbols[i : i + 400]))
        ).all()
        frames.append(pd.DataFrame(rows, columns=["symbol", "date", "close"]))
    df = pd.concat(frames)
    if df.empty:
        return pd.DataFrame()
    m = df.pivot(index="date", columns="symbol", values="close").sort_index()
    m.index = pd.DatetimeIndex(pd.to_datetime(m.index), name="date")
    return m.astype(float)


def membership_mask(s: Session, index_id: str, dates: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame | None:
    """Boolean frame (dates x symbols): True where the symbol was an index member on that date. None if no history."""
    rows = s.execute(select(Membership).where(Membership.index_id == index_id)).scalars().all()
    if not rows:
        return None
    mask = pd.DataFrame(False, index=dates, columns=symbols)
    for r in rows:
        if r.symbol not in mask.columns:
            continue
        end = pd.Timestamp(r.end_date) if r.end_date else dates.max()
        mask.loc[pd.Timestamp(r.start_date) : end, r.symbol] = True
    return mask


def pit_series(s: Session, series_id: str, index: pd.DatetimeIndex, transform=None) -> pd.DataFrame:
    """Value of a series as it was publicly known on each date in `index` (no revisions, no look-ahead).
    `transform` (e.g. YoY change) runs on the observation sequence first; a derived value only becomes
    usable once every input observation was published. Returns columns value, obs_date."""
    rows = s.execute(
        select(SeriesObs.date, SeriesObs.value, SeriesObs.available_at).where(SeriesObs.series_id == series_id)
    ).all()
    empty = pd.DataFrame({"value": np.nan, "obs_date": pd.NaT}, index=index)
    if not rows or len(index) == 0:
        return empty
    df = pd.DataFrame(rows, columns=["obs_date", "value", "available_at"])
    df["obs_date"] = pd.to_datetime(df["obs_date"]).astype("datetime64[ns]")
    df = df.sort_values("obs_date")
    # Conservative: an observation counts as available only once all earlier observations are too.
    df["available_at"] = pd.to_datetime(df["available_at"]).astype("datetime64[ns]").cummax()
    if transform is not None:
        df["value"] = transform(pd.Series(df["value"].values, index=df["obs_date"])).values
        df = df.dropna(subset=["value"])
        if df.empty:
            return empty
    df = df.sort_values(["available_at", "obs_date"])
    # A late-published older observation must not replace a newer one already known.
    prev_max = df["obs_date"].cummax().shift()
    df = df[prev_max.isna() | (df["obs_date"] > prev_max)]
    left = pd.DataFrame({"t": pd.DatetimeIndex(index).astype("datetime64[ns]")})
    m = pd.merge_asof(left, df, left_on="t", right_on="available_at", direction="backward")
    return pd.DataFrame({"value": m["value"].values, "obs_date": m["obs_date"].values}, index=index)


def series_meta(s: Session, series_id: str) -> dict | None:
    r = s.execute(
        select(func.max(SeriesObs.date), func.max(SeriesObs.available_at), func.max(SeriesObs.source), func.max(SeriesObs.fetched_at))
        .where(SeriesObs.series_id == series_id)
    ).one()
    if r[0] is None:
        return None
    return {"last_obs": r[0].isoformat(), "available_at": r[1].isoformat(), "source": r[2], "fetched_at": str(r[3])}


def bar_meta(s: Session) -> dict[str, dict]:
    rows = s.execute(
        select(Bar.symbol, func.min(Bar.date), func.max(Bar.date), func.count(), func.max(Bar.source), func.max(Bar.fetched_at)).group_by(Bar.symbol)
    ).all()
    return {r[0]: {"first": r[1].isoformat(), "last": r[2].isoformat(), "rows": r[3], "source": r[4], "fetched_at": str(r[5])} for r in rows}


def instruments(s: Session, **filters) -> list[Instrument]:
    q = select(Instrument)
    for k, v in filters.items():
        q = q.where(getattr(Instrument, k) == v)
    return list(s.execute(q.order_by(Instrument.symbol)).scalars())


def to_date(x) -> date:
    return pd.Timestamp(x).date()
