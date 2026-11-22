"""Reference and fundamental data (FMP), symbol search, S&P 500 membership. In memory with TTLs."""

import logging
from datetime import date, timedelta

import pandas as pd

from ..config import get_settings
from .cache import TTLCache
from .http import ProviderError
from .model import Unavailable
from .providers import fmp, massive

log = logging.getLogger(__name__)
_cache = TTLCache(maxsize=1000)
DAY = 86400

# GICS sector name -> SPDR sector ETF (reference mapping, not market data)
SECTOR_ETF = {
    "Technology": "XLK", "Information Technology": "XLK", "Financial Services": "XLF", "Financials": "XLF", "Energy": "XLE",
    "Healthcare": "XLV", "Health Care": "XLV", "Consumer Cyclical": "XLY", "Consumer Discretionary": "XLY", "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP", "Industrials": "XLI", "Basic Materials": "XLB", "Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}


def profile(symbol: str) -> dict | None:
    """Live company/ETF profile or None (never a stored guess)."""
    if not get_settings().fmp_api_key:
        return None

    def load():
        try:
            p = fmp.profile(symbol)
            p["cap_bucket"] = fmp.cap_bucket(p.get("market_cap"))
            p["sector_etf"] = SECTOR_ETF.get(p.get("sector", ""), "")
            return p, DAY
        except ProviderError as e:
            return {"error": str(e)}, 3600

    p = _cache.get_or_compute(f"profile:{symbol}", load)
    return None if "error" in p else p


def fundamentals(symbol: str) -> dict:
    if not get_settings().fmp_api_key:
        raise Unavailable("Fundamentals need FMP_API_KEY (Financial Modeling Prep).")

    def load():
        out, missing = {"symbol": symbol, "provider": "fmp"}, []
        sym = fmp.fmp_symbol(symbol)
        for name, path, keep in [
            ("ratios", "ratios-ttm", 1), ("metrics", "key-metrics-ttm", 1), ("earnings", "earnings", 12),
            ("estimates", "analyst-estimates", 4), ("dividends", "dividends", 8), ("splits", "splits", 5), ("holdings", "etf/holdings", 15),
        ]:
            try:
                params = {"symbol": sym} | ({"period": "annual", "limit": 4} if name == "estimates" else {})
                rows = fmp.get(path, **params)
                rows = rows if isinstance(rows, list) else [rows]
                if name == "holdings":
                    rows = sorted(rows, key=lambda r: -(r.get("weightPercentage") or 0))
                out[name] = rows[:keep] if keep > 1 else (rows[0] if rows else {})
            except ProviderError as e:
                missing.append(f"{name}: {e}")
        out["unavailable"] = missing
        return out, 12 * 3600

    return _cache.get_or_compute(f"fund:{symbol}", load)


def search(q: str) -> list[dict]:
    st = get_settings()
    q = q.strip()
    if not q:
        return []
    for name, fn in (("massive", lambda: massive.search(q)) if st.massive_api_key else (None, None),
                     ("fmp", lambda: [{"symbol": r["symbol"].replace("-", "."), "name": r.get("name", ""), "exchange": r.get("exchange", ""), "type": ""}
                                      for r in fmp.get("search-symbol", query=q, limit=20)]) if st.fmp_api_key else (None, None)):
        if not name:
            continue
        try:
            return [r | {"provider": name} for r in _cache.get_or_compute(f"search:{name}:{q.lower()}", lambda: (fn(), 3600))]
        except ProviderError as e:
            log.warning("search via %s failed: %s", name, e)
    return []


def sp500() -> tuple[list[str], list[dict] | None, str]:
    """(current members, membership intervals or None, provider)."""
    if not get_settings().fmp_api_key:
        raise Unavailable("S&P 500 membership needs FMP_API_KEY.")

    def load():
        members = [m["symbol"] for m in fmp.sp500_constituents()]
        try:
            intervals = membership_intervals(members, fmp.sp500_changes())
        except ProviderError as e:
            log.warning("historical S&P 500 changes unavailable: %s", e)
            intervals = None
        return (members, intervals, "fmp"), DAY

    return _cache.get_or_compute("sp500", load)


def membership_intervals(current: list[str], changes: list[dict]) -> list[dict]:
    """Walk back from today's members through dated additions/removals -> (symbol, start, end) intervals."""
    norm = lambda s: (s or "").strip().replace("-", ".")  # noqa: E731
    open_ = {s: None for s in current}  # symbol -> start date (None = before the change log)
    out = []
    for ch in sorted(changes, key=lambda c: c["date"], reverse=True):
        d = date.fromisoformat(ch["date"][:10])
        added, removed = norm(ch.get("symbol")), norm(ch.get("removedTicker"))
        if added and added in open_:
            out.append({"symbol": added, "start": d, "end": None if open_[added] is None else open_[added]})
            del open_[added]
        if removed:
            open_[removed] = d - timedelta(days=1)  # member until the day before removal
    for s, end in open_.items():  # member since before the change log begins
        out.append({"symbol": s, "start": date(1900, 1, 1), "end": end})
    return out


def membership_mask(intervals: list[dict], dates: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    mask = pd.DataFrame(False, index=dates, columns=symbols)
    for r in intervals:
        if r["symbol"] in mask.columns:
            mask.loc[pd.Timestamp(r["start"]) : (pd.Timestamp(r["end"]) if r["end"] else dates.max()), r["symbol"]] = True
    return mask
