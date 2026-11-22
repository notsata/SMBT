"""Massive (formerly Polygon.io) REST. Aggregates are split-adjusted (adjusted=true) but not dividend-adjusted.
History depth, delay and options/indices access depend on the plan. Docs: massive.com/docs"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pandas as pd

from ...config import get_settings
from ..calendar import ET
from ..http import ProviderError, http
from ..model import Bar

NAME = "massive"


def _headers() -> dict:
    k = get_settings().massive_api_key
    if not k:
        raise ProviderError(NAME, "not configured (MASSIVE_API_KEY)")
    return {"Authorization": f"Bearer {k}"}


def api_symbol(sym: str) -> str:
    return "I:" + sym[1:] if sym.startswith("^") else sym


def _results(path: str, params: dict | None = None, limit_pages: int = 50) -> list[dict]:
    url = f"{get_settings().massive_rest_url}{path}"
    out, p = [], dict(params or {})
    for _ in range(limit_pages):
        j = http().json(NAME, url, p, _headers())
        if j.get("status") in ("ERROR", "NOT_AUTHORIZED"):
            raise ProviderError(NAME, j.get("message") or j.get("error") or j["status"], 403)
        out += j.get("results") or j.get("tickers") or []
        nxt = j.get("next_url")
        if not nxt:
            break
        url, p = nxt, {}  # next_url carries the cursor; auth stays in the header
    return out


def parse_aggs(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
    out = pd.DataFrame({"open": df["o"], "high": df["h"], "low": df["l"], "close": df["c"], "volume": df.get("v", float("nan"))}).astype(float)
    out.index = pd.DatetimeIndex(idx, name="date")
    return out[~out.index.duplicated(keep="last")].sort_index()


def daily(symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
    end = end or datetime.now(timezone.utc).date()
    rows = _results(f"/v2/aggs/ticker/{api_symbol(symbol)}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                    {"adjusted": "true", "sort": "asc", "limit": 50000})
    return parse_aggs(rows)


def daily_bars(symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda s: (s, _safe(lambda: daily(s, start, end))), symbols))
    return {s: df for s, df in res if df is not None}


def _safe(fn):
    try:
        return fn()
    except ProviderError:
        return None


def minute_bars(symbol: str, day: date) -> list[Bar]:
    rows = _results(f"/v2/aggs/ticker/{api_symbol(symbol)}/range/1/minute/{day.isoformat()}/{day.isoformat()}",
                    {"adjusted": "true", "sort": "asc", "limit": 50000})
    src = f"{NAME}:{'realtime' if get_settings().massive_realtime else 'delayed'}:rest"
    return [Bar(int(r["t"]) * 1_000_000, r["o"], r["h"], r["l"], r["c"], r.get("v", 0), r.get("vw"), r.get("n", 0), True, src) for r in rows]


def parse_snapshot(t: dict) -> dict:
    lt, lq, d, pd_ = t.get("lastTrade") or {}, t.get("lastQuote") or {}, t.get("day") or {}, t.get("prevDay") or {}
    ns = lambda v: int(v) if v else None  # noqa: E731  (Massive snapshot times are epoch ns)
    return {
        "symbol": t.get("ticker"),
        "last": lt.get("p"), "last_ts": ns(lt.get("t")), "last_size": lt.get("s"),
        "bid": lq.get("p"), "bid_size": lq.get("s"), "ask": lq.get("P"), "ask_size": lq.get("S"), "quote_ts": ns(lq.get("t")),
        "day_open": d.get("o"), "day_high": d.get("h"), "day_low": d.get("l"), "day_close": d.get("c"), "day_volume": d.get("v"),
        "day_vwap": d.get("vw"), "day_ts": ns(t.get("updated")), "prev_close": pd_.get("c"), "prev_ts": None,
    }


def snapshots(symbols: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(symbols), 250):
        rows = _results("/v2/snapshot/locale/us/markets/stocks/tickers", {"tickers": ",".join(symbols[i : i + 250])}, 1)
        out |= {r["ticker"]: parse_snapshot(r) for r in rows if r.get("ticker")}
    return out


def index_values(symbols: list[str]) -> dict[str, dict]:
    rows = _results("/v3/snapshot/indices", {"ticker.any_of": ",".join(api_symbol(s) for s in symbols)}, 1)
    return {("^" + r["ticker"][2:]): {"value": r.get("value"), "ts": r.get("last_updated"), "timeframe": r.get("timeframe")}
            for r in rows if r.get("ticker") and r.get("value") is not None}


def option_chain(underlying: str) -> list[dict]:
    return _results(f"/v3/snapshot/options/{underlying}", {"limit": 250}, limit_pages=40)


def search(query: str) -> list[dict]:
    rows = _results("/v3/reference/tickers", {"search": query, "market": "stocks", "active": "true", "limit": 20}, 1)
    return [{"symbol": r["ticker"], "name": r.get("name", ""), "exchange": r.get("primary_exchange", ""), "type": r.get("type", "")} for r in rows]
