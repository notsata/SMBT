"""End-of-day sources: Tiingo daily prices (long, dividend-adjusted history) and Cboe's official index history
files (VIX, VIX3M, VIX9D, VVIX)."""

import io
from datetime import date

import pandas as pd

from ...config import get_settings
from ..http import ProviderError, http

CBOE_FILES = {"^VIX": "VIX", "^VIX3M": "VIX3M", "^VIX9D": "VIX9D", "^VVIX": "VVIX"}


def tiingo_symbol(sym: str) -> str:
    return sym.replace(".", "-").lower()


def parse_tiingo(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame({"open": df["adjOpen"], "high": df["adjHigh"], "low": df["adjLow"], "close": df["adjClose"],
                        "volume": df["adjVolume"]}).astype(float)
    out.index = pd.DatetimeIndex(pd.to_datetime(df["date"].str[:10]), name="date")
    return out[~out.index.duplicated(keep="last")].sort_index()


def tiingo_daily(symbol: str, start: date) -> pd.DataFrame:
    key = get_settings().tiingo_api_key
    if not key:
        raise ProviderError("tiingo", "not configured (TIINGO_API_KEY)")
    rows = http().json("tiingo", f"{get_settings().tiingo_url}/tiingo/daily/{tiingo_symbol(symbol)}/prices",
                       {"startDate": start.isoformat(), "format": "json"}, {"Authorization": f"Token {key}"})
    return parse_tiingo(rows)


def parse_cboe(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().upper() for c in df.columns]
    idx = pd.DatetimeIndex(pd.to_datetime(df["DATE"], format="%m/%d/%Y"), name="date")
    if "CLOSE" in df.columns:
        out = df[["OPEN", "HIGH", "LOW", "CLOSE"]].rename(columns=str.lower)
    else:  # VVIX_History.csv: DATE,VVIX
        out = pd.DataFrame({"open": float("nan"), "high": float("nan"), "low": float("nan"), "close": df.iloc[:, 1]})
    out = out.apply(pd.to_numeric, errors="coerce").assign(volume=float("nan"))
    out.index = idx
    return out[out["close"] > 0].sort_index()


def cboe_daily(symbol: str) -> pd.DataFrame:
    if symbol not in CBOE_FILES:
        raise ProviderError("cboe", f"{symbol} is not a Cboe index file")
    return parse_cboe(http().get("cboe", f"{get_settings().cboe_url}/{CBOE_FILES[symbol]}_History.csv").text)
