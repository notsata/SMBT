"""The phone page (packaging/artifact/build) re-implements the analysis engine (quant.js) and the NYSE calendar and
connector parsing (logic.js) in JavaScript. These tests run both under Node against the server's own results."""

import json
import shutil
import subprocess
from pathlib import Path

import exchange_calendars as xc
import numpy as np
import pandas as pd
import pytest

from app.quant.technical import technical_frame

from .conftest import fixture_bars

ART = Path(__file__).resolve().parents[2] / "packaging" / "artifact"


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_phone_page_logic_matches_server(tmp_path):
    cal = xc.get_calendar("XNYS", start="2007-01-03", end="2030-12-31")
    sessions = {d.date().isoformat() for d in cal.sessions}
    ref = {"trading": {d.date().isoformat(): d.date().isoformat() in sessions for d in pd.bdate_range("2007-01-03", "2030-12-31")}}
    (tmp_path / "ref.json").write_text(json.dumps(ref))
    r = subprocess.run(["node", str(ART / "logic.test.js"), str(ART / "build" / "logic.js"), str(tmp_path / "ref.json")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok:")


def _plain(x):
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_phone_quant_engine_matches_server(tmp_path):
    from app import scanner as SC
    from app.backtest import validation as V
    from app.backtest.strategies import STRATEGIES
    from app.quant import scores
    from app.quant.analogs import compare_key_dates, find_analogs
    from app.quant.probability import HORIZONS, score_probabilities
    from app.quant.technical import divergences
    from app.services import _declustered_crosses, classify_stock, state_history
    from app.quant.probability import fwd_return

    b = fixture_bars("^GSPC")
    f = technical_frame(b)  # price-only, as on the phone: no breadth, VIX or macro columns
    sf = scores.score_frame(f)
    n = len(f)
    cps = list(range(0, n, 97)) + [n - 1]
    tech_cols = ["sma20", "sma50", "sma100", "sma200", "dist200", "sma200_slope", "rsi14", "rsi2", "macd", "macd_signal", "macd_hist", "stochrsi_k", "bb_pctb",
                 "adx", "atr14", "ext_atr", "hv20", "roc20", "roc63", "hi252", "drawdown", "vol_ratio", "obv", "obv_slope20", "r2_63"]
    ref = {
        "bars": [{"t": d.date().isoformat(), "o": float(x.open), "h": float(x.high), "l": float(x.low), "c": float(x.close), "v": float(x.volume)} for d, x in b.iterrows()],
        "tech": {c: [[i, f[c].iloc[i]] for i in cps] for c in tech_cols},
        "flags": {c: [int(i) for i in np.flatnonzero(f[c].fillna(False).values)] for c in ("rsi_bull_div", "rsi_bear_div", "macd_bull_div", "macd_bear_div", "vp_bear_div")},
        "regime": [r if isinstance(r, str) else None for r in sf["regime"]],
        "scores": {k: [[i, sf[k].iloc[i]] for i in cps] for k in ("bottom", "top", "trend", "bottom_cov", "top_cov")},
        "explain_bottom": scores.explain(sf, f, side="bottom")["components"],
        "probabilities": score_probabilities(sf, f["close"]),
    }
    an = find_analogs(f)
    ref["analogs"] = {"features_used": an["features_used"], "matches": [{"date": m["date"], "similarity": m["similarity"], "event": m["event"]} for m in an["matches"]]}
    ref["key_dates"] = [{"date": m["date"], "similarity": m["similarity"]} for m in compare_key_dates(f)]
    ref["walk_forward"] = {}
    for sid in STRATEGIES:
        r = V.walk_forward(b, f, sf, sid)
        if "error" in r:
            ref["walk_forward"][sid] = {"error": r["error"]}
            continue
        cv = V.purged_cv(b, f, sf, sid)
        keys = ("cagr", "sharpe", "max_drawdown", "trades", "exposure")
        ref["walk_forward"][sid] = {"folds": [{"test": x["test"], "chosen_params": x["chosen_params"]} for x in r["folds"]],
                                    "oos": {k: r["oos"].get(k) for k in keys}, "in_sample": {k: r["in_sample_defaults"]["metrics"].get(k) for k in keys},
                                    "sharpe_diff": r["oos_vs_benchmark"].get("sharpe_diff"), "cv_params": [x["chosen_params"] for x in cv["folds"]],
                                    "cv_share": cv["share_folds_beating_benchmark"]}
    ref["scoreboard"] = V.signal_scoreboard(b, f, sf)
    rep = V.signal_report(b, f, sf, "rsi_oversold")
    tp = {k: v for k, v in rep["turning_points"].items() if k not in ("pivot_dates", "note")}
    ref["report"] = {"turning_points": tp, "stability": rep["stability_by_era"],
                     "excess": {str(h): rep["event_study"]["horizons"][str(h)].get("excess_mean") for h in HORIZONS}}
    events = []  # services.signals, on the price-only frame
    for side in ("bottom", "top"):
        events += [(d.date().isoformat(), side) for d in _declustered_crosses(sf[side], 70, 20)]
    events += [(e["confirmed"], f"{e['type']}_divergence") for e in divergences(f["close"], f["rsi14"])[2]]
    reg = sf["regime"]
    events += [(d.date().isoformat(), f"regime_{reg[d]}") for d in reg.index[(reg != reg.shift()) & reg.isin(["bear_capitulation", "distribution"])]]
    ref["events"] = [list(e) for e in sorted(events, key=lambda e: e[0])]
    state, _ = classify_stock(f)
    hist = state_history(f)
    stats = {}
    for h in (20, 60, 126):
        fr = fwd_return(f["close"], h).reindex(hist.index)
        vals = fr[hist == state].dropna().values
        stats[str(h)] = {"n_samples": len(vals), "median": float(np.median(vals)) if len(vals) else None, "hit_rate": float((vals > 0).mean()) if len(vals) else None}
    ref["state"] = {"state": state, "stats": stats}
    rows, pooled, syms = [], {k: [] for k in SC.FLAGS}, []  # scanner._compute for the fixture universe
    for sym in ("^GSPC", "^IXIC"):
        sb = fixture_bars(sym)
        syms.append({"symbol": sym, "bars": [{"t": d.date().isoformat(), "o": float(x.open), "h": float(x.high), "l": float(x.low), "c": float(x.close), "v": float(x.volume)} for d, x in sb.iterrows()]})
        sf_ = technical_frame(sb, b["close"] if sym != "^GSPC" else None)
        fwd = sf_["close"].shift(-SC.H) / sf_["close"] - 1
        active = []
        for fid, (_, _, fn) in SC.FLAGS.items():
            sig = fn(sf_).fillna(False).astype(bool)
            onset = sig & ~sig.shift(fill_value=False)
            pooled[fid].extend((fwd[onset] - fwd.mean()).dropna().tolist())
            if sig.iloc[-1]:
                active.append(fid)
        rows.append({"symbol": sym, "flags": active, "rs63": sf_["rs63"].iloc[-1] if "rs63" in sf_ else None})
    stats = {fid: {"n": len(v), "excess_mean": float(np.mean(v)) if v else None} for fid, v in pooled.items()}
    for row in rows:
        act = [x for x in row["flags"] if SC.FLAGS[x][1] == "bullish"]
        row["bullish_evidence"] = round(sum(max(0.0, stats[x]["excess_mean"] or 0) for x in act if stats[x]["n"] >= 30) * 100, 3)
    ref["scanner"] = {"benchmark": "^GSPC", "symbols": syms, "rows": rows, "flag_evidence": stats}
    (tmp_path / "ref.json").write_text(json.dumps(_plain(ref)))
    r = subprocess.run(["node", str(ART / "quant.test.js"), str(ART / "build" / "quant.js"), str(tmp_path / "ref.json")], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok:")


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_phone_market_inputs_match_server(tmp_path, monkeypatch):
    """Breadth, VIX, stock/bond, point-in-time macro, Fear & Greed and macro risk: same inputs through the server's
    Python (MacroStore.pit, breadth_frame, sentiment_frame, macro_frame, fear_greed, macro_risk) and the phone's JS."""
    from app.market import macro as M
    from app.market.providers import fred
    from app.quant import scores
    from app.quant.analogs import find_analogs
    from app.quant.breadth import breadth_frame
    from app.quant.macro import fear_greed, macro_frame, macro_risk, sentiment_frame

    rng = np.random.default_rng(5)
    b = fixture_bars("^GSPC")
    f = technical_frame(b)
    idx = f.index
    members = {}
    for k in range(11):  # synthetic members, some listed later, as sector funds are
        start = [0, 0, 0, 0, 0, 0, 0, 0, 0, 1700, 3900][k]
        px = 50 * np.exp(np.cumsum(rng.normal(0.0003, 0.013, len(idx))))
        px[:start] = np.nan
        members[f"M{k}"] = pd.Series(px, index=idx)
    close_m = pd.DataFrame(members)
    tlt = pd.Series(90 * np.exp(np.cumsum(rng.normal(0.0001, 0.008, len(idx)))), index=idx)
    vix = fixture_bars("^VIX")["close"]
    days = pd.bdate_range("1995-01-02", "2019-01-10")
    walk = lambda lo, sd: np.round(lo + np.cumsum(rng.normal(0, sd, len(days))), 2)  # noqa: E731
    raw = {"T10Y2Y": pd.Series(walk(1.0, 0.03), index=days), "T10Y3M": pd.Series(walk(1.5, 0.03), index=days),
           "DFF": pd.Series(np.clip(walk(4.0, 0.02), 0, None), index=days),
           "UNRATE": pd.Series(np.round(5 + np.cumsum(rng.normal(0, 0.15, 300)), 1), index=pd.date_range("1994-01-01", periods=300, freq="MS"))}
    monkeypatch.setattr(fred, "observations", lambda sid, revised: (pd.DataFrame({"value": raw[sid].values, "available_at": None}, index=raw[sid].index.date), "test"))
    store = M.MacroStore()
    empty = pd.Series(np.nan, index=idx)

    def pit(sid, transform=None):
        return store.pit(sid, idx, transform)[0] if sid in raw else empty

    bf = breadth_frame(close_m, None, min_names=9)
    f = f.join(bf.reindex(idx)).join(sentiment_frame(idx, vix, None)).join(macro_frame(idx, pit))
    f["stock_bond_20"] = f["roc20"] - tlt.reindex(idx).pct_change(20, fill_method=None)
    f = f.join(fear_greed(f)).join(macro_risk(f))
    sf = scores.score_frame(f)
    n = len(f)
    cps = list(range(0, n, 53)) + [n - 1]
    cols = ["pct_above_20", "pct_above_50", "pct_above_200", "ad_line", "mcclellan_osc", "mcclellan_sum", "nhnl_pct", "pct_oversold", "pct_overbought", "zweig_ema",
            "vix", "vix_vs50", "stock_bond_20", "curve_10y2y", "curve_10y3m", "curve_inverted_days", "curve_uninversion", "fed_funds", "fed_chg252", "sahm", "unrate",
            "fear_greed", "fear_greed_n", "macro_risk"]
    ref = {
        "bars": [{"t": d.date().isoformat(), "o": float(x.open), "h": float(x.high), "l": float(x.low), "c": float(x.close), "v": float(x.volume)} for d, x in b.iterrows()],
        "members": {k: [[d.date().isoformat(), float(v)] for d, v in s.dropna().items()] for k, s in members.items()},
        "min_names": 9,
        "vix": [[d.date().isoformat(), float(v)] for d, v in vix.items()],
        "tlt": [[d.date().isoformat(), float(v)] for d, v in tlt.items()],
        "macro": {k: [[d.isoformat() if hasattr(d, "isoformat") else str(d), float(v)] for d, v in zip(s.index.date, s.values)] for k, s in raw.items()},
        "cols": {c: [[i, f[c].iloc[i]] for i in cps] for c in cols},
        "flags": {c: [int(i) for i in np.flatnonzero(f[c].fillna(False).values)] for c in ("zweig_thrust", "washout_day")},
        "regime": [r if isinstance(r, str) else None for r in sf["regime"]],
        "scores": {k: [[i, sf[k].iloc[i]] for i in cps] for k in ("bottom", "top", "trend", "bottom_cov", "top_cov")},
    }
    an = find_analogs(f)
    ref["analogs"] = {"features_used": an["features_used"], "matches": [[m["date"], m["similarity"]] for m in an["matches"]]}
    (tmp_path / "ref.json").write_text(json.dumps(_plain(ref)))
    r = subprocess.run(["node", str(ART / "inputs.test.js"), str(ART / "build" / "quant.js"), str(tmp_path / "ref.json")], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok:")
