"""Data provider clients. Each returns plain pandas objects; persistence and quality checks live in ingest.py.

Error messages never include request params, so API keys cannot leak into logs or the ingest table.
"""

import csv
import io
import logging
import random
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (X11; Linux x86_64) SMBT-research/1.0"
RETRY_STATUS = {429, 500, 502, 503, 504}

YAHOO = "Yahoo Finance chart API (unofficial; EOD bars, quotes delayed ~15 min)"
TIINGO = "Tiingo EOD API"
CBOE = "Cboe Global Markets (official index history)"


class DataError(Exception):
    pass


class Http:
    """httpx client with per-host throttling and exponential-backoff retries on 429/5xx/transport errors."""

    def __init__(self, min_interval_s: float = 0.4, attempts: int = 4, client: httpx.Client | None = None):
        self.min_interval_s = min_interval_s
        self.attempts = attempts
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval_s - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        host = httpx.URL(url).host
        err = "unknown"
        for i in range(self.attempts):
            self._throttle(host)
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError as e:
                err = type(e).__name__
            else:
                if r.status_code == 200:
                    return r
                err = f"HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUS:
                    break
                ra = r.headers.get("retry-after", "")
                if ra.isdigit():
                    time.sleep(min(int(ra), 60))
                    continue
            if i < self.attempts - 1:
                time.sleep(min(30.0, 2**i + random.random()))
        raise DataError(f"{host}: {err}")


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def parse_yahoo_chart(j: dict, now: datetime | None = None) -> tuple[pd.DataFrame, dict | None]:
    chart = j.get("chart") or {}
    if chart.get("error"):
        raise DataError(f"yahoo: {chart['error'].get('description', 'error')}")
    res = (chart.get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return pd.DataFrame(), None
    meta = res.get("meta", {})
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    q = res["indicators"]["quote"][0]
    adj = ((res["indicators"].get("adjclose") or [{}])[0]).get("adjclose")
    idx = [datetime.fromtimestamp(t, tz).date() for t in res["timestamp"]]
    df = pd.DataFrame(
        {k: q.get(k) for k in ("open", "high", "low", "close", "volume")} | {"adj_close": adj or q.get("close")},
        index=pd.Index(idx, name="date"),
    ).astype(float)
    now = now or datetime.now(tz)
    session_end = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("end", 0)
    # Never store the in-progress session: its "close" is a live price, not a settled bar.
    if len(df) and df.index[-1] >= now.date() and now.timestamp() < session_end + 20 * 60:
        df = df.iloc[:-1]
    df = df[~df.index.duplicated(keep="last")]
    quote_ = None
    if meta.get("regularMarketPrice") is not None and meta.get("regularMarketTime"):
        quote_ = {
            "price": float(meta["regularMarketPrice"]),
            "time": datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).isoformat(),
            "source": YAHOO,
            "delay": "delayed ~15 min (exchange-dependent); not real-time",
        }
    return df, quote_


def yahoo_daily(http: Http, symbol: str, start: date | None) -> tuple[pd.DataFrame, dict | None]:
    start = start or date(1990, 1, 1)
    r = http.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}",
        params={
            "interval": "1d",
            "period1": _epoch(start),
            "period2": int(time.time()) + 86400,
            "includeAdjustedClose": "true",
            "events": "div,split",
        },
    )
    return parse_yahoo_chart(r.json())


def parse_tiingo(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "close": df["close"],
            "volume": df["volume"],
            "adj_close": df["adjClose"],
        }
    ).astype(float)
    out.index = pd.Index(pd.to_datetime(df["date"]).dt.date, name="date")
    return out


def tiingo_daily(http: Http, key: str, symbol: str, start: date | None) -> pd.DataFrame:
    r = http.get(
        f"https://api.tiingo.com/tiingo/daily/{quote(symbol.lower())}/prices",
        params={"startDate": (start or date(1990, 1, 1)).isoformat(), "token": key, "format": "json"},
    )
    return parse_tiingo(r.json())


def parse_cboe_history(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().upper() for c in df.columns]
    d = pd.to_datetime(df["DATE"], format="%m/%d/%Y").dt.date
    if "CLOSE" in df.columns:
        out = df[["OPEN", "HIGH", "LOW", "CLOSE"]].astype(float).rename(columns=str.lower)
    else:  # e.g. VVIX_History.csv has DATE,VVIX
        out = pd.DataFrame({"open": None, "high": None, "low": None, "close": df.iloc[:, 1].astype(float)})
    out["adj_close"] = out["close"]
    out["volume"] = float("nan")
    out.index = pd.Index(d, name="date")
    return out.astype(float)


def cboe_index(http: Http, name: str) -> pd.DataFrame:
    r = http.get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv")
    return parse_cboe_history(r.text)


def parse_cboe_putcall(text: str) -> pd.Series:
    """Archived Cboe P/C CSVs have disclaimer lines before a 'DATE,...,P/C Ratio' header."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip().upper().startswith("DATE")), None)
    if start is None:
        raise DataError("cboe put/call: header not found")
    out = {}
    for row in csv.reader(lines[start + 1 :]):
        if len(row) < 2 or not row[0].strip():
            continue
        try:
            d = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
            out[d] = float(row[-1])
        except ValueError:
            continue
    return pd.Series(out, dtype=float).sort_index()


def cboe_putcall(http: Http, kind: str) -> pd.Series:
    r = http.get(f"https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/{kind}pc.csv")
    return parse_cboe_putcall(r.text)


def parse_fredgraph(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    s = pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]).dt.date, dtype=float)
    return s.dropna()


def parse_fred_observations(j: dict, first_release: bool) -> pd.DataFrame:
    """Returns columns value, available_at. With first_release (ALFRED output_type=4), available_at is the
    real first-publication date; otherwise it is left NaT for the caller to fill with a conservative lag."""
    rows = []
    for o in j.get("observations", []):
        if o.get("value") in (".", "", None):
            continue
        d = date.fromisoformat(o["date"])
        avail = date.fromisoformat(o["realtime_start"]) if first_release else None
        rows.append((d, float(o["value"]), avail))
    df = pd.DataFrame(rows, columns=["date", "value", "available_at"]).set_index("date")
    return df[~df.index.duplicated(keep="first")]


def fred_series(http: Http, series_id: str, key: str, revised: bool) -> tuple[pd.DataFrame, str]:
    if key:
        params = {"series_id": series_id, "api_key": key, "file_type": "json"}
        if revised:
            params |= {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "output_type": 4}
        r = http.get("https://api.stlouisfed.org/fred/series/observations", params=params)
        src = "FRED/ALFRED API (first-release vintages)" if revised else "FRED API"
        return parse_fred_observations(r.json(), first_release=revised), src
    r = http.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id})
    s = parse_fredgraph(r.text)
    return pd.DataFrame({"value": s, "available_at": None}), "FRED graph CSV (latest vintage + publication lag)"


def sp500_constituents(http: Http, url: str) -> list[dict]:
    df = pd.read_csv(io.StringIO(http.get(url).text))
    cols = {c.lower(): c for c in df.columns}
    sym, name, sector = cols.get("symbol"), cols.get("security") or cols.get("name"), cols.get("gics sector") or cols.get("sector")
    return [
        {"symbol": str(r[sym]).strip().replace(".", "-"), "name": str(r[name]) if name else "", "sector": str(r[sector]) if sector else ""}
        for _, r in df.iterrows()
    ]


def membership_intervals(snapshots: pd.DataFrame) -> list[dict]:
    """snapshots: columns date, tickers (comma-separated members as of that date) -> membership intervals."""
    snaps = snapshots.assign(date=pd.to_datetime(snapshots["date"]).dt.date).sort_values("date")
    open_: dict[str, date] = {}
    out = []
    for d, tickers in zip(snaps["date"], snaps["tickers"]):
        members = {t.strip().replace(".", "-") for t in str(tickers).split(",") if t.strip()}
        for t in list(open_):
            if t not in members:  # snapshots mark change dates: removed the day before d (inclusive end)
                out.append({"symbol": t, "start_date": open_.pop(t), "end_date": d - timedelta(days=1)})
        for t in members - open_.keys():
            open_[t] = d
    out += [{"symbol": t, "start_date": s, "end_date": None} for t, s in open_.items()]
    return out


def sp500_history(http: Http, url: str) -> list[dict]:
    return membership_intervals(pd.read_csv(io.StringIO(http.get(url).text)))


def date_n_days_ago(n: int) -> date:
    return date.today() - timedelta(days=n)
