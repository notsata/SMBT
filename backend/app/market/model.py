"""Normalized market data objects shared by every provider adapter and consumer."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class Status(StrEnum):
    LIVE = "LIVE"  # confirmed real-time feed, session open, recent events
    DELAYED = "DELAYED"  # provider delay known (e.g. 15 min), session open
    STALE = "STALE"  # previously verified, no longer current
    CLOSED = "CLOSED"  # market session inactive; last verified values
    UNAVAILABLE = "UNAVAILABLE"  # no trustworthy value


class Kind(StrEnum):
    REALTIME = "realtime"
    DELAYED = "delayed"
    END_OF_DAY = "end_of_day"
    RELEASE = "official_release"  # macro data published on a release schedule
    DERIVED = "derived"


def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def iso(ns: int | None) -> str | None:
    return None if ns is None else datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def parse_ts(s: str) -> int:
    """RFC3339 with up to nanosecond precision -> epoch ns (datetime only keeps microseconds)."""
    s = s.replace("Z", "+00:00")
    frac = ""
    if "." in s:
        head, rest = s.split(".", 1)
        i = next((k for k, ch in enumerate(rest) if not ch.isdigit()), len(rest))
        frac, tz = rest[:i], rest[i:]
        s = head + tz
    base = int(datetime.fromisoformat(s).timestamp()) * 1_000_000_000
    return base + int((frac + "000000000")[:9]) if frac else base


@dataclass(slots=True)
class Trade:
    symbol: str
    price: float
    size: float
    ts: int  # exchange/SIP event time, epoch ns
    exchange: str
    id: str
    conditions: tuple = ()
    provider: str = ""
    feed: str = ""
    recv: int = 0


@dataclass(slots=True)
class Quote:
    symbol: str
    bid: float
    bid_size: float
    ask: float
    ask_size: float
    ts: int
    provider: str = ""
    feed: str = ""
    recv: int = 0


@dataclass(slots=True)
class Bar:
    start: int  # epoch ns of the bar's opening instant
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    trades: int = 0
    final: bool = False  # False while the interval is still open (provisional)
    source: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["t"] = d.pop("start") // 1_000_000_000
        return d


@dataclass
class Observation:
    """One displayed value with full provenance. Every card and live metric is sent in this shape."""

    id: str
    value: float | str | None
    provider: str
    authority: str = ""  # exchange or publishing authority
    event_ts: str | None = None
    recv_ts: str | None = None
    kind: Kind = Kind.DERIVED
    delay_s: int | None = None
    session: str | None = None
    status: Status = Status.UNAVAILABLE
    method: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["kind"], d["status"] = str(self.kind), str(self.status)
        return d


class Unavailable(Exception):
    """No trustworthy value exists (missing key, provider failure, insufficient history, licence)."""


class LicenseBlocked(Unavailable):
    pass
