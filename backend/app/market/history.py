"""Historical bars on demand from the configured providers, held only in memory.

Rules: completed sessions only (an in-progress day never enters confirmed history); one provider per series
(no stitching or silent mixing); provider choice follows HISTORY_PROVIDERS, preferring the first that covers the
requested start, otherwise the one with the longest history. Every frame records provider and adjustment basis.
"""

import logging
import threading
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..config import get_settings
from . import calendar as cal
from .cache import TTLCache
from .http import ProviderError
from .model import Unavailable
from .providers import alpaca, eod, massive

log = logging.getLogger(__name__)
_cache = TTLCache(maxsize=400)
_versions: dict[str, int] = {}
_vlock = threading.Lock()

ADJUSTMENT = {"tiingo": "splits+dividends", "alpaca": "splits+dividends", "massive": "splits only", "cboe": "index level (no adjustment)"}
AUTHORITY = {"tiingo": "Tiingo EOD (consolidated)", "alpaca": "Consolidated tape (SIP) via Alpaca", "massive": "Consolidated tape (SIP) via Massive",
             "cboe": "Cboe Global Indices (official)"}


def is_index(sym: str) -> bool:
    return sym.startswith("^")


def configured(p: str) -> bool:
    st = get_settings()
    return {"alpaca": st.has_alpaca, "massive": bool(st.massive_api_key), "tiingo": bool(st.tiingo_api_key), "cboe": True}.get(p, False)


def candidates(symbol: str) -> list[str]:
    st = get_settings()
    if is_index(symbol):
        out = ["cboe"] if symbol in eod.CBOE_FILES else []
        return out + (["massive"] if st.massive_indices and configured("massive") else [])
    return [p for p in st.history_order if p in ("massive", "tiingo", "alpaca") and configured(p)]


def _fetch(p: str, symbol: str, start: date) -> pd.DataFrame:
    if p == "tiingo":
        return eod.tiingo_daily(symbol, start)
    if p == "cboe":
        return eod.cboe_daily(symbol)
    if p == "massive":
        return massive.daily(symbol, start)
    if p == "alpaca":
        return alpaca.daily_bars([symbol], max(start, date(2016, 1, 1))).get(symbol, pd.DataFrame())
    raise ProviderError(p, "unknown provider")


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Bad values become NaN or are dropped and reported; nothing is interpolated."""
    issues = []
    if df.empty:
        return df, ["no rows"]
    df = df[~df.index.duplicated(keep="last")].sort_index().copy()
    bad = ~(df["close"] > 0)
    if bad.any():
        issues.append(f"{int(bad.sum())} rows with missing/non-positive close dropped")
        df = df[~bad]
    for c in ("open", "high", "low"):
        b = df[c].notna() & ~(df[c] > 0)
        df.loc[b, c] = np.nan
    hl = df["high"].notna() & df["low"].notna() & ((df["high"] * 1.005 < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1) * 1.005))
    if hl.any():
        issues.append(f"{int(hl.sum())} rows with inconsistent high/low set to NaN")
        df.loc[hl, ["open", "high", "low"]] = np.nan
    jumps = df["close"].pct_change().abs() > 0.5
    if jumps.any():
        issues.append(f"{int(jumps.sum())} daily moves > 50% (check adjustment): " + ", ".join(str(d.date()) for d in df.index[jumps][:3]))
    return df, issues


def _load(symbol: str) -> tuple[pd.DataFrame, float]:
    st = get_settings()
    start = date.fromisoformat(st.history_start)
    cands = candidates(symbol)
    if not cands:
        raise Unavailable(f"No historical data provider configured for {symbol}. Set MASSIVE_API_KEY, TIINGO_API_KEY or Alpaca keys.")
    errors, best = [], None
    for p in cands:
        try:
            df = _fetch(p, symbol, start)
        except ProviderError as e:
            errors.append(str(e))
            continue
        if df.empty:
            errors.append(f"{p}: no data for {symbol}")
            continue
        if best is None or df.index[0] < best[1].index[0]:
            best = (p, df)
        if df.index[0].date() <= start + timedelta(days=45):
            break
    if best is None:
        raise Unavailable(f"{symbol}: no provider returned history ({'; '.join(errors)})")
    p, df = best
    now = datetime.now(timezone.utc)
    last_done = cal.last_completed_session(now)
    df = df[df.index.date <= last_done]  # drop any in-progress session the provider may include
    df, issues = validate(df)
    if df.empty:
        raise Unavailable(f"{symbol}: provider returned no completed sessions")
    have_last = df.index[-1].date() >= last_done
    ttl = (cal.next_close_after(now) + timedelta(minutes=45) - now).total_seconds() if have_last else 15 * 60
    df.attrs = {"symbol": symbol, "provider": p, "authority": AUTHORITY.get(p, p), "adjustment": ADJUSTMENT.get(p, ""),
                "first": df.index[0].date().isoformat(), "last": df.index[-1].date().isoformat(), "fetched_at": now.isoformat(),
                "issues": issues, "provider_errors": errors, "complete_through_last_session": have_last}
    with _vlock:
        _versions[symbol] = _versions.get(symbol, 0) + 1
    log.info("history %s: %d bars from %s (%s..%s)", symbol, len(df), p, df.attrs["first"], df.attrs["last"])
    return df, max(ttl, 60)


def daily(symbol: str) -> pd.DataFrame:
    """Confirmed daily bars (completed sessions). Raises Unavailable; never returns placeholder data."""
    symbol = symbol.upper()
    try:
        return _cache.get_or_compute(f"d:{symbol}", lambda: _load(symbol))
    except Unavailable:
        stale = _cache.peek(f"d:{symbol}")
        if stale is not None:  # last verified copy, explicitly marked
            stale = stale.copy()
            stale.attrs = {**stale.attrs, "stale": True}
            return stale
        raise


def version(symbol: str) -> int:
    return _versions.get(symbol.upper(), 0)


def daily_many(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], str]:
    """Bulk daily closes for breadth. Uses a provider with an efficient multi-symbol path."""
    start = date.fromisoformat(get_settings().history_start)
    for p in [x for x in get_settings().history_order if x in ("alpaca", "massive") and configured(x)]:
        try:
            if p == "alpaca":
                out = {}
                for i in range(0, len(symbols), 50):
                    out |= alpaca.daily_bars(symbols[i : i + 50], date(2016, 1, 1))
                return out, p
            return massive.daily_bars(symbols, start), p
        except ProviderError as e:
            log.warning("bulk history via %s failed: %s", p, e)
    raise Unavailable("Breadth needs Alpaca or Massive keys (bulk constituent history).")


def intraday(symbol: str, provider: str, day: date) -> list:
    key = f"i:{provider}:{symbol}:{day}"

    def load():
        bars = (alpaca.minute_bars if provider == "alpaca" else massive.minute_bars)(symbol, day)
        return bars, 60

    return _cache.get_or_compute(key, load)
