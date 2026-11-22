"""The phone page (packaging/artifact/build/logic.js) re-implements indicators, the stock-state rules and the NYSE
calendar in JavaScript. This runs it under Node against the server's own results so the two cannot drift."""

import json
import shutil
import subprocess
from pathlib import Path

import exchange_calendars as xc
import numpy as np
import pandas as pd
import pytest

from app.quant.technical import expanding_pct, technical_frame
from app.services import classify_row

from .conftest import fixture_bars

ART = Path(__file__).resolve().parents[2] / "packaging" / "artifact"


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node")
def test_phone_page_logic_matches_server(tmp_path):
    b = fixture_bars("^GSPC")
    f = technical_frame(b)
    dist_pct = expanding_pct(f["dist200"])
    rows = f.to_dict("records")
    clean = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
    checks = []
    for i in range(300, len(f), 37):  # divergence and relative-strength inputs are not on the phone page
        state, _ = classify_row(rows[i], {"bear_div": False, "bull_div": False, "dist_pct": dist_pct.iloc[i], "rs_p": np.nan})
        checks.append({"i": i, "state": state, "dist_pct": clean(dist_pct.iloc[i]),
                       **{k: clean(f.iloc[i][k]) for k in ("rsi14", "sma200", "dist200", "sma200_slope", "drawdown", "atr_pct", "bb_pctb", "hv20",
                                                         "vol_ratio", "obv_slope20")}})
    cal = xc.get_calendar("XNYS", start="2007-01-03", end="2030-12-31")
    sessions = {d.date().isoformat() for d in cal.sessions}
    ref = {"bars": [{"t": d.date().isoformat(), "o": float(x.open), "h": float(x.high), "l": float(x.low), "c": float(x.close), "v": float(x.volume)}
                    for d, x in b.iterrows()],
           "checks": checks,
           "trading": {d.date().isoformat(): d.date().isoformat() in sessions for d in pd.bdate_range("2007-01-03", "2030-12-31")}}
    (tmp_path / "ref.json").write_text(json.dumps(ref))
    r = subprocess.run(["node", str(ART / "logic.test.js"), str(ART / "build" / "logic.js"), str(tmp_path / "ref.json")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ok:")
