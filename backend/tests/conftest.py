"""Test setup. Production code has no fixture or sample path: tests inject real historical data (tests/fixtures,
1999-2018 index history) through the provider seam with monkeypatch, so every analysis path runs on genuine
observations without network access."""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.update({
    "BENCHMARKS": "^GSPC,^IXIC", "SCANNER_UNIVERSE": "^GSPC,^IXIC", "ACCESS_MODE": "personal", "PUBLIC_DISPLAY_LICENSES": "",
    "ALPACA_API_KEY_ID": "", "ALPACA_API_SECRET_KEY": "", "MASSIVE_API_KEY": "", "TIINGO_API_KEY": "", "FMP_API_KEY": "",
    "FRED_API_KEY": "", "ANTHROPIC_API_KEY": "", "BREADTH_ENABLED": "false",
})
FIX = Path(__file__).parent / "fixtures"


def fixture_bars(symbol: str) -> pd.DataFrame:
    name = {"^GSPC": "gspc", "^IXIC": "ixic", "^VIX": "vix"}.get(symbol)
    if name is None:
        from app.market.http import ProviderError

        raise ProviderError("fixture", f"no fixture for {symbol}", 404)
    df = pd.read_csv(FIX / f"bars_{name}.csv.gz", parse_dates=["date"])
    f = (df["adj_close"] / df["close"]).fillna(1.0)
    out = pd.DataFrame({"open": df["open"] * f, "high": df["high"] * f, "low": df["low"] * f, "close": df["adj_close"].fillna(df["close"]),
                        "volume": df["volume"]}).astype(float)
    out.index = pd.DatetimeIndex(df["date"], name="date")
    return out


def fixture_series(series_id: str):
    from app.market.http import ProviderError

    m = pd.read_csv(FIX / "macro.csv.gz", parse_dates=["date"])
    g = m[m["series_id"] == series_id]
    if g.empty:
        raise ProviderError("fred", f"no fixture for {series_id}", 404)
    return pd.DataFrame({"value": g["value"].values, "available_at": None}, index=g["date"].dt.date.values), "fixture"


@pytest.fixture(scope="session", autouse=True)
def fixture_providers():
    from app.market import history
    from app.market.providers import fred

    mp = pytest.MonkeyPatch()
    mp.setattr(history, "candidates", lambda s: ["fixture"])
    mp.setattr(history, "_fetch", lambda p, s, start: fixture_bars(s))
    mp.setattr(history, "ADJUSTMENT", {"fixture": "splits+dividends"})
    mp.setattr(fred, "observations", lambda sid, revised: fixture_series(sid))
    yield
    mp.undo()


@pytest.fixture(scope="session")
def spx(fixture_providers):
    from app import services as S

    f, sf, meta = S.market_frame("^GSPC")
    return S.bars("^GSPC"), f, sf


def synthetic_bars(n=800, seed=0, drift=0.0003, vol=0.012) -> pd.DataFrame:
    """Random-walk OHLCV for unit tests of math only (never used for any reported statistic)."""
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = c * np.exp(rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    v = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.bdate_range("2010-01-04", periods=n)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx)
