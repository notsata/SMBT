"""US equity session clock (NYSE calendar incl. holidays and early closes, via exchange_calendars)."""

from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")


class Session(StrEnum):
    PRE = "premarket"  # 04:00 - open
    REGULAR = "regular"
    POST = "afterhours"  # close - close + 4h
    CLOSED = "closed"


@lru_cache(maxsize=1)
def _cal():
    import exchange_calendars as xc

    return xc.get_calendar("XNYS")


def _utc(ts: datetime | None) -> datetime:
    return (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)


def is_trading_day(d: date) -> bool:
    return bool(_cal().is_session(pd.Timestamp(d)))


def open_close(d: date) -> tuple[datetime, datetime]:
    o, c = _cal().session_open_close(pd.Timestamp(d))
    return o.to_pydatetime(), c.to_pydatetime()


def session_at(ts: datetime | None = None) -> Session:
    ts = _utc(ts)
    d = ts.astimezone(ET).date()
    if not is_trading_day(d):
        return Session.CLOSED
    o, c = open_close(d)
    if ts < o - timedelta(hours=5, minutes=30):
        return Session.CLOSED
    if ts < o:
        return Session.PRE
    if ts < c:
        return Session.REGULAR
    if ts < c + timedelta(hours=4):
        return Session.POST
    return Session.CLOSED


def last_completed_session(ts: datetime | None = None) -> date:
    """Most recent trading day whose regular session has closed by `ts`."""
    ts = _utc(ts)
    d = ts.astimezone(ET).date()
    for _ in range(15):
        if is_trading_day(d) and open_close(d)[1] <= ts:
            return d
        d -= timedelta(days=1)
    raise RuntimeError("no completed session in the last 15 days")


def current_session_date(ts: datetime | None = None) -> date | None:
    """Trading date whose session (pre/regular/post) contains ts, else None."""
    ts = _utc(ts)
    return ts.astimezone(ET).date() if session_at(ts) != Session.CLOSED else None


def next_close_after(ts: datetime | None = None) -> datetime:
    ts = _utc(ts)
    d = ts.astimezone(ET).date()
    for _ in range(15):
        if is_trading_day(d):
            c = open_close(d)[1]
            if c > ts:
                return c
        d += timedelta(days=1)
    return ts + timedelta(days=1)


def info(ts: datetime | None = None) -> dict:
    ts = _utc(ts)
    s = session_at(ts)
    d = ts.astimezone(ET).date()
    out = {"session": str(s), "server_time": ts.isoformat(), "trading_day": is_trading_day(d), "date": d.isoformat()}
    if is_trading_day(d):
        o, c = open_close(d)
        out |= {"open": o.isoformat(), "close": c.isoformat(), "early_close": c.astimezone(ET).hour < 16}
    nxt = _cal().next_open(pd.Timestamp(ts))
    out["next_open"] = nxt.to_pydatetime().isoformat()
    return out
