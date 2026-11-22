from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, get_settings


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


ENDPOINTS = [
    "/api/health", "/api/status", "/api/symbols", "/api/overview?symbol=^GSPC", "/api/overview?symbol=^IXIC", "/api/chart/^GSPC",
    "/api/indicators/^GSPC?names=rsi14,vix,bottom,macro_risk", "/api/signals/^GSPC", "/api/analyze/^IXIC", "/api/scanner",
    "/api/scanner?timeframe=weekly&direction=bearish", "/api/analogs?symbol=^GSPC", "/api/strategies", "/api/signal-stats/^GSPC?signal=rsi_oversold",
    "/api/scoreboard/^GSPC", "/api/alerts/kinds", "/api/quote/^GSPC", "/api/search?q=apple",
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_endpoints_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"


def test_overview_cards_carry_provenance_and_status(client):
    ov = client.get("/api/overview?symbol=^GSPC").json()
    assert set(ov["cards"]) == {"regime", "bottom", "top", "trend", "vix", "breadth", "sentiment", "macro"}
    for name, c in ov["cards"].items():
        assert {"value", "status", "basis", "as_of", "provider", "method"} <= c.keys(), name
        assert c["status"] in {"LIVE", "DELAYED", "STALE", "CLOSED", "UNAVAILABLE"}
    # fixture history ends 2018, so nothing may claim to be current
    assert ov["cards"]["bottom"]["status"] == "STALE" and "has not published" in ov["cards"]["bottom"]["note"]
    assert ov["cards"]["breadth"]["status"] == "UNAVAILABLE" and ov["cards"]["breadth"]["value"] is None
    assert ov["quote"]["status"] == "UNAVAILABLE"  # no streaming provider configured: no price is shown as live
    assert ov["probabilities"]["kind"] == "empirical" and ov["confirmed_as_of"] == "2018-12-31"
    assert "fixture" in ov["meta"]["sources"]


def test_unavailable_paths_are_truthful(client):
    r = client.get("/api/intraday/^GSPC")
    assert r.status_code == 503 and "streaming provider" in r.json()["detail"]
    r = client.get("/api/options/SPY")
    assert r.status_code == 503 and "Options" in r.json()["detail"]
    r = client.get("/api/analyze/NOPE")
    assert r.status_code == 503 and "no provider returned history" in r.json()["detail"]
    a = client.get("/api/analyze/^IXIC").json()
    assert a["fundamentals"]["unavailable"] and a["profile"] is None and a["live"] is None


def test_public_mode_blocks_unlicensed_sources(client, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "access_mode", "public")
    r = client.get("/api/overview?symbol=^GSPC")
    assert r.status_code == 451 and "not licensed" in r.json()["detail"]
    monkeypatch.setattr(st, "public_display_licenses", "fixture,cboe")
    assert client.get("/api/overview?symbol=^GSPC").status_code == 200


def test_backtest_and_validation(client):
    r = client.post("/api/backtest", json={"symbol": "^GSPC", "strategy": "rsi2"})
    assert r.status_code == 200
    j = r.json()
    assert j["oos"]["n_days"] > 1000 and j["purged_cv"]["folds"] and j["data"]["basis"] == "confirmed closes"
    assert client.post("/api/backtest", json={"strategy": "nope"}).status_code == 400
    assert client.get("/api/signal-stats/^GSPC?signal=nope").status_code == 400


def test_rate_limit(client, monkeypatch):
    from app import main

    monkeypatch.setattr(get_settings(), "rate_limit_per_min", 3)
    main._hits.clear()
    codes = [client.get("/api/symbols").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    main._hits.clear()


def test_browser_websocket_protocol(client):
    with client.websocket_connect("/ws") as w:
        hello = w.receive_json()
        assert hello["type"] == "hello" and hello["licensing"]["access_mode"] == "personal" and "session" in hello
        w.send_json({"op": "subscribe", "symbols": ["^GSPC", "bad symbol!!"]})
        batch = w.receive_json()
        assert batch["type"] == "batch"
        kinds = {(i["type"], i["symbol"]) for i in batch["items"]}
        assert ("quote", "^GSPC") in kinds and ("bars", "^GSPC") in kinds
        quote = next(i for i in batch["items"] if i["type"] == "quote")["data"]
        assert quote["status"] == "UNAVAILABLE"
        w.send_json({"op": "alerts", "rules": [{"kind": "score_above", "params": {"score": "bottom", "threshold": 50}}]})
        ack = next(i for i in _items(w) if i["type"] == "alerts_ack")
        assert ack["rules"][0]["params"]["threshold"] == 50
        w.send_json({"op": "alerts", "rules": [{"kind": "score_above", "params": {"score": "x", "threshold": 50}}]})
        assert any(i["type"] == "error" for i in _items(w))


def _items(w):
    for _ in range(20):
        m = w.receive_json()
        if m.get("type") == "batch" and m["items"]:
            return m["items"]
    return []


def test_session_alerts_hysteresis():
    from app import services as S
    from app.alerts import SessionAlerts, crossed

    cur = S.overview("^GSPC")["cards"]["bottom"]["value"]
    sa = SessionAlerts()
    sa.set_rules([{"kind": "score_above", "params": {"score": "bottom", "threshold": cur - 1, "symbol": "^GSPC"}, "cooldown_minutes": 1}])
    first = sa.evaluate()
    assert len(first) == 1 and "above" in first[0]["title"]
    assert sa.evaluate() == []  # still above: not re-armed, no repeat
    assert crossed(cur - 10, cur - 1, True, False, 5) == (False, True)  # re-arms after moving back by rearm


def test_nothing_is_persisted(client):
    leftovers = [p for p in ROOT.rglob("*") if p.suffix in (".db", ".sqlite", ".sqlite3") and "node_modules" not in p.parts and ".venv" not in p.parts]
    assert leftovers == []
    import app

    assert not any(m.startswith("sqlalchemy") for m in __import__("sys").modules), "no database layer should be loaded"
    assert app


def test_analyst_disabled_without_key(client):
    assert client.post("/api/analyst", json={"question": "How close is the market to a bottom?"}).status_code == 503


def test_analyst_loop_grounded_in_tools(monkeypatch):
    """Fake Claude client: requests tools, then answers. Tool results and citations come from live-layer data."""
    from app import analyst

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    seen = {}

    class FakeMessages:
        n = 0

        def create(self, **kw):
            FakeMessages.n += 1
            assert kw["fallbacks"] == "default" and kw["tools"]
            if FakeMessages.n == 1:
                return SimpleNamespace(stop_reason="tool_use", model="m", content=[
                    SimpleNamespace(type="tool_use", id="t1", name="get_market_overview", input={"symbol": "^GSPC"}),
                    SimpleNamespace(type="tool_use", id="t2", name="get_signal_statistics", input={"symbol": "^GSPC", "signal": "nope"}),
                ])
            seen["results"] = kw["messages"][-1]["content"]
            return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text="Grounded answer")])

    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda **kw: SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    out = analyst.ask("How close is the S&P to a bottom?")
    assert out["answer"] == "Grounded answer" and out["citations"][0]["as_of"] == "2018-12-31"
    res = seen["results"]
    assert len(res) == 2 and res[1]["is_error"] is True and '"status"' in res[0]["content"]


@pytest.mark.parametrize("tool,args", [
    ("analyze_symbol", {"symbol": "^IXIC"}), ("get_signal_scoreboard", {"symbol": "^GSPC"}),
    ("get_correction_frequency", {"symbol": "^GSPC", "threshold_pct": 10, "horizon_days": 126}),
    ("get_historical_analogs", {"symbol": "^GSPC"}), ("run_scanner", {"direction": "bullish"}), ("get_data_status", {}),
])
def test_analyst_tools(client, tool, args):
    from app.analyst import run_tool

    data, cites = run_tool(tool, args)
    assert data and cites
