"""Record the real API's responses on the bundled sample data for the offline mobile build.

Every value comes from the same endpoints and analyst tools the server uses; nothing is hand-written.
Usage (backend venv):  python packaging/artifact/export_snapshot.py <out_dir>
Writes <out_dir>/*.json, each one JSON array with one {"key", "data"} record per line.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 'snapshot.db'}"
os.environ["APP_ACCESS_TOKEN"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import analyst, scanner  # noqa: E402
from app import services as S  # noqa: E402
from app.backtest import validation as V  # noqa: E402
from app.backtest.strategies import STRATEGIES  # noqa: E402
from app.data.ingest import load_sample_data  # noqa: E402
from app.db import init_db, session  # noqa: E402
from app.main import app  # noqa: E402

SYMS = ["^GSPC", "^IXIC"]
BT_DEFAULTS = {"train_years": 6, "test_years": 2, "commission_bps": 1.0, "slippage_bps": 2.0, "cv": True}


def write(out: Path, name: str, records: dict) -> None:
    lines = [json.dumps({"key": k, "data": v}, separators=(",", ":"), allow_nan=False) for k, v in records.items()]
    (out / f"{name}.json").write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")
    print(f"{name}.json  {len(records):4d} records  {(out / f'{name}.json').stat().st_size / 1e6:.2f} MB")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    init_db()
    s = session()
    load_sample_data(s)
    c = TestClient(app)

    def get(path: str):
        r = c.get("/api" + path)
        assert r.status_code == 200, (path, r.text)
        return r.json()

    core = {p: get(p) for p in ["/status", "/strategies", "/alerts/kinds", "/alerts", "/alerts/events", "/watchlist", "/settings", "/push/key", "/admin/ingest", "/auth/me"]}
    syms = get("/symbols")
    syms["symbols"] = [x for x in syms["symbols"] if x["symbol"] in SYMS]
    core["/symbols"] = syms
    for sym in SYMS:
        core[f"/overview?symbol={sym}"] = get(f"/overview?symbol={sym}")
        for p in (f"/analyze/{sym}", f"/analogs?symbol={sym}&k=12", f"/scoreboard/{sym}", f"/signals/{sym}"):
            core[p] = get(p)
    for tf in ("daily", "weekly"):
        core[f"scan:{tf}"] = scanner.scan(s, tf, direction="", limit=100_000)
    write(out, "core", core)

    write(out, "charts", {f"/chart/{sym}": get(f"/chart/{sym}") for sym in SYMS})
    write(out, "indicators", {sym: S.indicator_series(s, sym, ["close", *S.INDICATOR_COLUMNS]) for sym in SYMS})

    stats = {}
    for sym in SYMS:
        f, sf, _ = S.market_frame(s, sym)
        for name in V.signal_library(f, sf):
            for th in (range(50, 95, 5) if name in ("bottom_score", "top_score") else [70]):
                stats[f"{sym}|{name}|{th}"] = get(f"/signal-stats/{sym}?signal={name}&threshold={th}")
    write(out, "signal-stats", stats)

    bts = {}
    for sym in SYMS:
        for sid in STRATEGIES:
            r = c.post("/api/backtest", json={"symbol": sym, "strategy": sid, **BT_DEFAULTS})
            assert r.status_code == 200, r.text
            bts[f"{sym}|{sid}"] = r.json()
    write(out, "backtests", bts)

    tools = {}
    calls = [("get_data_status", {}), ("run_scanner", {"direction": "bullish"}), ("run_scanner", {"direction": "bearish"})]
    for sym in SYMS:
        f, sf, _ = S.market_frame(s, sym)
        calls += [("get_market_overview", {"symbol": sym}), ("analyze_symbol", {"symbol": sym}), ("get_signal_scoreboard", {"symbol": sym}),
                  ("get_historical_analogs", {"symbol": sym})]
        calls += [("get_signal_statistics", {"symbol": sym, "signal": n}) for n in V.signal_library(f, sf)]
        calls += [("get_correction_frequency", {"symbol": sym, "threshold_pct": t, "horizon_days": h}) for t in (5, 10, 15, 20) for h in (20, 60, 126, 252)]
    for name, args in calls:
        data, cites = analyst.run_tool(s, name, args)
        text = json.dumps(data, default=str, allow_nan=False)
        assert len(text) < 31_000, (name, args, len(text))  # page tools may return at most 32 KB
        tools[f"{name}|{json.dumps(args, sort_keys=True)}"] = {"result": json.loads(text), "cites": cites}
    tools["__meta__"] = {"system": analyst.SYSTEM, "tools": analyst.TOOLS}
    write(out, "analyst", tools)
    s.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
