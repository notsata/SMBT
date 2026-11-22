"""Financial Modeling Prep (stable API): company profile (market cap, sector), TTM ratios, earnings,
analyst estimates, ETF holdings, dividends/splits, S&P 500 constituents and historical changes, symbol search."""

from ...config import get_settings
from ..http import ProviderError, http

NAME = "fmp"


def fmp_symbol(sym: str) -> str:
    return sym.replace(".", "-")


def get(path: str, **params) -> list | dict:
    st = get_settings()
    if not st.fmp_api_key:
        raise ProviderError(NAME, "not configured (FMP_API_KEY)")
    j = http().json(NAME, f"{st.fmp_url}/{path}", params | {"apikey": st.fmp_api_key})
    if isinstance(j, dict) and j.get("Error Message"):
        raise ProviderError(NAME, "request rejected (plan or symbol)", 403)
    return j


def first(rows) -> dict:
    return rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else {}


def profile(symbol: str) -> dict:
    p = first(get("profile", symbol=fmp_symbol(symbol)))
    if not p:
        raise ProviderError(NAME, f"no profile for {symbol}", 404)
    return {
        "symbol": symbol,
        "name": p.get("companyName", ""),
        "market_cap": p.get("marketCap", p.get("mktCap")),
        "sector": p.get("sector") or "",
        "industry": p.get("industry") or "",
        "exchange": p.get("exchange") or p.get("exchangeShortName") or "",
        "is_etf": bool(p.get("isEtf")),
        "beta": p.get("beta"),
    }


def cap_bucket(market_cap) -> str:
    if not isinstance(market_cap, (int, float)) or market_cap <= 0:
        return ""
    return "mega" if market_cap >= 200e9 else "large" if market_cap >= 10e9 else "mid" if market_cap >= 2e9 else "small"


def sp500_constituents() -> list[dict]:
    rows = get("sp500-constituent")
    return [{"symbol": r["symbol"].replace("-", "."), "name": r.get("name", ""), "sector": r.get("sector", "")} for r in rows if r.get("symbol")]


def sp500_changes() -> list[dict]:
    """Additions/removals with dates (used to rebuild point-in-time membership)."""
    return [r for r in get("historical-sp500-constituent") if r.get("date")]
