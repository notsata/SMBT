import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_tmp = Path(tempfile.mkdtemp(prefix="smbt-test-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ["APP_ACCESS_TOKEN"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["VAPID_PUBLIC_KEY"] = ""
os.environ["VAPID_PRIVATE_KEY"] = ""


@pytest.fixture(scope="session")
def db():
    from app.config import get_settings
    from app.data.ingest import load_sample_data
    from app.db import init_db, reset_engine, session

    get_settings.cache_clear()
    reset_engine()
    init_db()
    s = session()
    load_sample_data(s)
    yield s
    s.close()


@pytest.fixture(scope="session")
def spx(db):
    from app import services as S

    f, sf, meta = S.market_frame(db, "^GSPC")
    return S.bars(db, "^GSPC"), f, sf


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
