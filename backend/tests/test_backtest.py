import numpy as np
import pandas as pd
import pytest

from app.backtest import engine, validation as V
from app.backtest.strategies import STRATEGIES, grid_points, latch
from app.quant.probability import fwd_min_return, walk_forward_probability

from .conftest import synthetic_bars


def test_signal_cannot_capture_its_own_bar():
    """A target set on the close of a +50% day must not earn that day's move (no same-bar execution)."""
    b = synthetic_bars(50, seed=3)
    b.loc[b.index[20], ["open", "high", "low", "close"]] = b.loc[b.index[19], "close"] * np.array([1.0, 1.5, 1.0, 1.5])
    tgt = pd.Series(0.0, index=b.index)
    tgt.iloc[20] = 1.0  # decided at close of the jump day
    res = engine.run(b, tgt, 0, 0)
    assert res["weight"].iloc[20] == 0 and res["weight"].iloc[21] == 1
    # position earns open(21)->open(22), not the jump into close(20)
    assert res["ret"].iloc[20] == 0


def test_costs_charged_on_turnover():
    b = synthetic_bars(30, seed=1)
    tgt = pd.Series([0, 1] * 15, index=b.index, dtype=float)
    free = engine.run(b, tgt, 0, 0)
    costly = engine.run(b, tgt, 5, 5)
    n_changes = free["weight"].diff().abs().fillna(free["weight"]).sum()
    assert (free["ret"] - costly["ret"]).sum() == pytest.approx(n_changes * 10 / 1e4)


def test_buy_hold_matches_asset():
    b = synthetic_bars(400, seed=9)
    res = engine.run(b, pd.Series(1.0, index=b.index), 0, 0)
    assert res["ret"].iloc[1:].values == pytest.approx(res["asset_ret"].iloc[1:].values)


def test_latch_and_grid():
    e = pd.Series([0, 1, 0, 0, 0, 1], dtype=bool)
    x = pd.Series([0, 0, 0, 1, 0, 0], dtype=bool)
    assert latch(e, x).tolist() == [0, 1, 1, 0, 0, 1]
    assert len(grid_points({"a": [1, 2], "b": [3, 4, 5]})) == 6


def test_metrics_known_values():
    r = pd.Series([0.01] * 252)
    m = engine.metrics(r)
    assert m["total_return"] == pytest.approx(1.01**252 - 1)
    assert m["max_drawdown"] == 0


def test_bootstrap_detects_identical_series():
    r = pd.Series(np.random.default_rng(0).normal(0.0004, 0.01, 1500))
    d = engine.block_bootstrap_diff(r, r)
    assert d["sharpe_diff"] == 0 and d["sharpe_diff_ci95"] == [0, 0]


def test_random_signal_is_not_significant():
    """A coin-flip signal on a random walk should rarely look significant (guards against a leaky test)."""
    b = synthetic_bars(3000, seed=11)
    sig = pd.Series(np.random.default_rng(5).random(len(b)) < 0.01, index=b.index)
    es = V.event_study(b, sig, "bullish", cooldown=10, horizons=[20], n_random=500)
    assert es["horizons"]["20"]["p_value_vs_random"] > 0.01


def test_zigzag_and_turning_points():
    c = pd.Series(np.r_[np.linspace(100, 130, 30), np.linspace(130, 100, 30), np.linspace(100, 140, 30)], index=pd.bdate_range("2020-01-01", periods=90))
    piv = V.zigzag(c, 0.10)
    kinds = [p["kind"] for p in piv]
    assert "peak" in kinds and "trough" in kinds
    peak = next(p for p in piv if p["kind"] == "peak")
    assert peak["price"] == pytest.approx(130)
    bars = pd.DataFrame({"open": c, "high": c, "low": c, "close": c})
    sig = pd.Series(False, index=c.index)
    sig.iloc[59] = True  # right at the trough
    tp = V.turning_points(bars, sig, "bullish", 0.10, window=5)
    assert tp["precision"] == 1.0


def test_bh_qvalues_monotone():
    q = V.bh_qvalues([0.01, 0.04, None, 0.03, 0.5])
    assert q[2] is None and all(qq >= p for qq, p in zip([q[0], q[1], q[3], q[4]], [0.01, 0.04, 0.03, 0.5]))
    assert q[0] == pytest.approx(0.04)


def test_walk_forward_probability_uses_only_resolved_labels():
    n = 400
    idx = pd.bdate_range("2015-01-01", periods=n)
    score = pd.Series(55.0, index=idx)
    label = pd.Series(np.r_[np.zeros(n - 50), np.ones(50)], index=idx)  # outcome flips at the end
    wf = walk_forward_probability(score, label, horizon=60, min_n=10)
    # On the final date, labels from the last 60 sessions are unresolved, so the estimate can't see the flip yet.
    assert wf["p"].iloc[-1] == 0.0


def test_fwd_min_return():
    c = pd.Series([100, 90, 95, 120, 80.0])
    assert fwd_min_return(c, 2).iloc[0] == pytest.approx(-0.10)
    assert pd.isna(fwd_min_return(c, 2).iloc[-1])


def test_walk_forward_on_real_sample(spx):
    b, f, sf = spx
    r = V.walk_forward(b, f, sf, "trend200")
    assert r["folds"] and r["oos"]["n_days"] > 1000
    first_test = pd.Timestamp(r["folds"][0]["test"][0])
    assert first_test > pd.Timestamp(r["folds"][0]["train"][1])  # test strictly after train
    assert "warning" in r["in_sample_defaults"]
    assert set(r["oos_vs_benchmark"]) >= {"sharpe_diff_ci95", "ann_return_diff_ci95"}


@pytest.mark.parametrize("sid", [s for s in STRATEGIES if s != "buy_hold"])
def test_strategies_causal(spx, sid):
    b, f, sf = spx
    s = STRATEGIES[sid]
    cut = 3500
    full = s.fn(f, sf, s.defaults)
    part = s.fn(f.iloc[:cut], sf.iloc[:cut], s.defaults)
    pd.testing.assert_series_equal(full.iloc[:cut].astype(float), part.astype(float), check_names=False, check_freq=False)


def test_strategy_without_required_data_reports_unavailable(spx):
    b, f, sf = spx
    r = V.walk_forward(b, f, sf, "zweig")  # sample data has no constituent breadth
    assert "error" in r and "breadth" in r["error"]
