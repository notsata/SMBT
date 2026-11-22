import numpy as np
import pandas as pd
import pytest

from app.quant import technical as T
from app.quant.breadth import breadth_frame
from app.quant.scores import score_frame

from .conftest import synthetic_bars


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert T.rsi(up).dropna().eq(100).all()
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert T.rsi(down).dropna().lt(1e-9).all()
    r = T.rsi(synthetic_bars()["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_matches_wilder_reference():
    # Wilder's smoothing written out explicitly (seeded with the first value, as the EWM implementation is).
    c = synthetic_bars(120, seed=5)["close"]
    d = c.diff().values[1:]
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[0], l[0]
    for i in range(1, len(d)):
        ag = ag + (g[i] - ag) / 14
        al = al + (l[i] - al) / 14
    assert T.rsi(c).iloc[-1] == pytest.approx(100 - 100 / (1 + ag / al), rel=1e-9)


def test_bollinger_and_atr():
    b = synthetic_bars(300, seed=2)
    bb = T.bollinger(b["close"])
    last = b["close"].iloc[-20:]
    assert bb["bb_mid"].iloc[-1] == pytest.approx(last.mean())
    assert bb["bb_upper"].iloc[-1] == pytest.approx(last.mean() + 2 * last.std(ddof=0))
    atr = T.atr(b)
    assert (atr.dropna() > 0).all()
    tr = T.true_range(b).iloc[1:]
    assert (tr >= (b["high"] - b["low"]).iloc[1:] - 1e-12).all()


def test_adx_range_and_trend_detection():
    n = 300
    trend = pd.DataFrame({"close": np.linspace(100, 200, n)}, index=pd.bdate_range("2020-01-01", periods=n))
    trend["open"], trend["high"], trend["low"] = trend.close * 0.999, trend.close * 1.002, trend.close * 0.997
    a = T.adx(trend)
    assert a["adx"].iloc[-1] > 40 and a["plus_di"].iloc[-1] > a["minus_di"].iloc[-1]


def test_divergence_detection_on_constructed_series():
    # Price makes a lower low while the decline decelerates -> RSI higher low -> bullish divergence.
    seg = [np.linspace(100, 80, 25), np.linspace(80, 90, 15), np.linspace(90, 79, 30), np.linspace(79, 88, 20)]
    c = pd.Series(np.concatenate(seg), index=pd.bdate_range("2020-01-01", periods=sum(map(len, seg))))
    bull, bear, ev = T.divergences(c, T.rsi(c, 14), k=3)
    assert bull.any() and not bear.any()
    e = ev[0]
    assert e["type"] == "bullish" and e["price"][1] < e["price"][0] and e["osc"][1] > e["osc"][0]
    # Signal is stamped on the confirmation bar, k sessions after the pivot.
    assert pd.Timestamp(e["confirmed"]) == c.index[c.index.get_loc(pd.Timestamp(e["pivot2"])) + 3]


@pytest.mark.parametrize("cut", [400, 555, 700])
def test_technical_frame_has_no_lookahead(cut):
    """Truncation invariance: values at t computed on data[:t] equal values at t computed on all data."""
    b = synthetic_bars(800, seed=1)
    full = T.technical_frame(b)
    part = T.technical_frame(b.iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], part, check_freq=False)


def test_breadth_frame_no_lookahead_and_thrust():
    rng = np.random.default_rng(4)
    n, k = 600, 120
    rets = rng.normal(0.0003, 0.015, (n, k)) + rng.normal(0, 0.01, (n, 1))
    m = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=pd.bdate_range("2015-01-01", periods=n), columns=[f"S{i}" for i in range(k)])
    full = breadth_frame(m)
    part = breadth_frame(m.iloc[:450])
    pd.testing.assert_frame_equal(full.iloc[:450], part, check_freq=False)
    assert full["pct_above_50"].dropna().between(0, 100).all()
    assert full["zweig_thrust"].dtype == bool


def test_breadth_respects_membership_mask():
    idx = pd.bdate_range("2020-01-01", periods=300)
    m = pd.DataFrame({f"S{i}": np.linspace(100, 150, 300) for i in range(110)}, index=idx)
    m["LOSER"] = np.linspace(100, 50, 300)
    mask = pd.DataFrame(True, index=idx, columns=m.columns)
    mask["LOSER"] = False
    with_mask = breadth_frame(m, mask)
    without = breadth_frame(m)
    assert with_mask["pct_above_50"].iloc[-1] == 100
    assert without["pct_above_50"].iloc[-1] < 100


def test_scores_no_lookahead(spx):
    """Headline scores and regime at t must not change when future data is appended."""
    b, f, sf = spx
    cut = 3000
    sf_part = score_frame(f.iloc[:cut])
    cols = ["bottom", "top", "trend", "regime"]
    pd.testing.assert_frame_equal(sf.iloc[:cut][cols], sf_part[cols], check_freq=False)


def test_expanding_percentile_is_causal():
    x = pd.Series(np.r_[np.arange(300.0), [1000.0]])
    p = T.expanding_pct(x, 252)
    assert p.iloc[-1] == 1.0 and pd.isna(p.iloc[100])
