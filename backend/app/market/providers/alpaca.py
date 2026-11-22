"""Alpaca Market Data v2 (REST). Feeds: iex (real-time, IEX venue only), sip (all exchanges, paid plan),
delayed_sip (15-minute delayed SIP). Historical data from 2016; the free plan cannot query the latest 15
minutes of SIP history. Docs: docs.alpaca.markets/docs/about-market-data-api"""

from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ...config import get_settings
from ..calendar import ET
from ..http import ProviderError, http
from ..model import Bar, parse_ts

NAME = "alpaca"
AUTHORITY = {"iex": "IEX exchange (single venue)", "sip": "Consolidated tape (SIP)", "delayed_sip": "Consolidated tape (SIP), 15-min delayed"}


def _headers() -> dict:
    st = get_settings()
    if not st.has_alpaca:
        raise ProviderError(NAME, "not configured (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)")
    return {"APCA-API-KEY-ID": st.alpaca_api_key_id, "APCA-API-SECRET-KEY": st.alpaca_api_secret_key}


def history_feed() -> str:
    # SIP history is available on every plan except the latest 15 minutes; IEX only when the account streams IEX.
    return "sip"


def _rest_feed() -> str:
    f = get_settings().alpaca_feed
    return "iex" if f == "iex" else "sip" if f == "sip" else "delayed_sip"


def parse_daily(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(bars)
    idx = pd.to_datetime(df["t"], utc=True).dt.tz_convert(ET).dt.tz_localize(None).dt.normalize()
    out = pd.DataFrame({"open": df["o"], "high": df["h"], "low": df["l"], "close": df["c"], "volume": df["v"]}).astype(float)
    out.index = pd.DatetimeIndex(idx, name="date")
    return out[~out.index.duplicated(keep="last")].sort_index()


def _paged(url: str, params: dict, key: str) -> dict[str, list]:
    out: dict[str, list] = {}
    token = None
    for _ in range(500):
        p = params | ({"page_token": token} if token else {})
        j = http().json(NAME, url, p, _headers())
        page = j.get(key) or {}
        if isinstance(page, list):  # single-symbol endpoint
            out.setdefault(params.get("symbols", "_"), []).extend(page)
        else:
            for sym, rows in page.items():
                out.setdefault(sym, []).extend(rows or [])
        token = j.get("next_page_token")
        if not token:
            break
    return out


def daily_bars(symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
    """Split- and dividend-adjusted daily bars. End is capped 16 minutes back (free-plan SIP restriction)."""
    cap = datetime.now(timezone.utc) - timedelta(minutes=16)
    end_dt = min(cap, datetime.combine(end, datetime.max.time(), timezone.utc)) if end else cap
    params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": f"{start.isoformat()}T00:00:00Z",
              "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "adjustment": "all", "feed": history_feed(), "limit": 10000}
    raw = _paged(f"{get_settings().alpaca_data_url}/v2/stocks/bars", params, "bars")
    return {s: parse_daily(rows) for s, rows in raw.items()}


def minute_bars(symbol: str, day: date) -> list[Bar]:
    start = datetime.combine(day, datetime.min.time(), ET).replace(hour=4)
    params = {"symbols": symbol, "timeframe": "1Min", "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "adjustment": "raw", "feed": _rest_feed(), "limit": 10000}
    raw = _paged(f"{get_settings().alpaca_data_url}/v2/stocks/bars", params, "bars")
    return [Bar(parse_ts(b["t"]), b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw"), b.get("n", 0), True, f"{NAME}:{_rest_feed()}:rest")
            for b in raw.get(symbol, [])]


def parse_snapshot(sym: str, s: dict) -> dict:
    lt, lq, db, pb = s.get("latestTrade") or {}, s.get("latestQuote") or {}, s.get("dailyBar") or {}, s.get("prevDailyBar") or {}
    return {
        "symbol": sym,
        "last": lt.get("p"), "last_ts": parse_ts(lt["t"]) if lt.get("t") else None, "last_size": lt.get("s"),
        "bid": lq.get("bp"), "bid_size": lq.get("bs"), "ask": lq.get("ap"), "ask_size": lq.get("as"),
        "quote_ts": parse_ts(lq["t"]) if lq.get("t") else None,
        "day_open": db.get("o"), "day_high": db.get("h"), "day_low": db.get("l"), "day_close": db.get("c"), "day_volume": db.get("v"),
        "day_vwap": db.get("vw"), "day_ts": parse_ts(db["t"]) if db.get("t") else None,
        "prev_close": pb.get("c"), "prev_ts": parse_ts(pb["t"]) if pb.get("t") else None,
    }


def snapshots(symbols: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(symbols), 100):
        j = http().json(NAME, f"{get_settings().alpaca_data_url}/v2/stocks/snapshots",
                        {"symbols": ",".join(symbols[i : i + 100]), "feed": _rest_feed()}, _headers())
        j = j.get("snapshots", j)  # tolerate both documented envelope shapes
        out |= {sym: parse_snapshot(sym, s) for sym, s in j.items() if isinstance(s, dict)}
    return out
