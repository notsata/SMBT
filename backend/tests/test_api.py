from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(db):
    from app.main import app

    with TestClient(app) as c:
        yield c


ENDPOINTS = [
    "/api/health", "/api/status", "/api/symbols", "/api/overview", "/api/overview?symbol=^IXIC", "/api/chart/^GSPC",
    "/api/indicators/^GSPC?names=rsi14,vix,bottom,credit_spread", "/api/signals/^GSPC", "/api/analyze/^IXIC",
    "/api/scanner", "/api/scanner?timeframe=weekly&direction=bearish", "/api/analogs?symbol=^GSPC", "/api/strategies",
    "/api/signal-stats/^GSPC?signal=rsi_oversold", "/api/scoreboard/^GSPC", "/api/alerts/kinds", "/api/watchlist", "/api/settings",
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_endpoints_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text


def test_overview_contract(client):
    ov = client.get("/api/overview?symbol=^GSPC").json()
    cards = ov["cards"]
    assert set(cards) == {"regime", "bottom", "top", "trend", "vix", "breadth", "sentiment", "macro"}
    for c in cards.values():
        assert {"value", "as_of", "source", "available"} <= c.keys()
    # breadth needs constituent data the sample lacks: it must say unavailable, not show a number
    assert cards["breadth"]["value"] is None and cards["breadth"]["available"] is False and cards["breadth"]["note"]
    assert cards["bottom"]["kind"] == "heuristic" and ov["probabilities"]["kind"] == "empirical"
    assert "End-of-day" in ov["data_kind"]
    assert 0 <= cards["bottom"]["value"] <= 100


def test_unknown_symbol_404(client):
    assert client.get("/api/analyze/ZZZZ").status_code == 404


def test_backtest_and_errors(client):
    r = client.post("/api/backtest", json={"symbol": "^GSPC", "strategy": "rsi2"})
    assert r.status_code == 200
    j = r.json()
    assert j["oos"]["n_days"] > 0 and j["purged_cv"]["folds"] and j["equity"]
    assert client.post("/api/backtest", json={"strategy": "nope"}).status_code == 400
    assert client.get("/api/signal-stats/^GSPC?signal=nope").status_code == 400


def test_alert_crud_validation(client):
    assert client.post("/api/alerts", json={"name": "bad", "kind": "score_above", "params": {"score": "x", "threshold": 50}}).status_code == 400
    r = client.post("/api/alerts", json={"name": "vix", "kind": "vix_range", "params": {"min": 10, "max": 40}})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert client.put(f"/api/alerts/{rid}", json={"name": "vix2", "kind": "vix_range", "params": {"min": 10, "max": 41}}).json()["params"]["max"] == 41
    assert client.delete(f"/api/alerts/{rid}").status_code == 200


def test_settings_and_watchlist_roundtrip(client):
    client.put("/api/settings", json={"default_symbol": "QQQ"})
    assert client.get("/api/settings").json()["default_symbol"] == "QQQ"
    assert "NVDA" in client.post("/api/watchlist/nvda").json()
    assert "NVDA" not in client.delete("/api/watchlist/NVDA").json()


def test_import_series_csv(client):
    r = client.post("/api/admin/import/AAII_SPREAD", content=b"date,value\n2018-01-04,20.5\n2018-01-11,15.0\n")
    assert r.status_code == 200 and r.json()["rows"] == 2
    assert client.post("/api/admin/import/NOT_A_SERIES", content=b"date,value\n").status_code == 400


def test_auth_enforced_when_token_set(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_access_token", "s3cret-token")
    monkeypatch.setattr(get_settings(), "cookie_secure", False)
    client.cookies.clear()
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/login", json={"token": "wrong"}).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    r = client.post("/api/auth/login", json={"token": "s3cret-token"})
    assert r.status_code == 200 and "smbt_session" in r.cookies
    assert client.get("/api/status").status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/status").status_code == 401


def test_alert_hysteresis_and_cooldown(db):
    from app import alerts as A
    from app.db import AlertEvent, AlertRule, utcnow

    cur = A.S.overview(db, "^GSPC")["cards"]["bottom"]["value"]
    rule = AlertRule(name="t", kind="score_above", params=A.validate("score_above", {"score": "bottom", "threshold": cur - 1, "symbol": "^GSPC"}),
                     cooldown_minutes=60, armed=True, last_state={})
    db.add(rule)
    db.commit()
    now = utcnow()
    first = A.evaluate_all(db, now)
    assert any(e.rule_id == rule.id for e in first)
    again = A.evaluate_all(db, now + timedelta(hours=5))
    assert not any(e.rule_id == rule.id for e in again)  # still above threshold: not re-armed, no repeat
    assert A._crossed(cur, cur - 1, True, False, 5) == (False, False)
    assert A._crossed(cur - 10, cur - 1, True, False, 5) == (False, True)  # re-arms after moving back by rearm
    db.delete(rule)
    db.commit()


def test_analyst_disabled_without_key(client):
    r = client.post("/api/analyst", json={"question": "How close is the market to a bottom?"})
    assert r.status_code == 503


def test_analyst_loop_grounded_in_tools(db, monkeypatch):
    """Fake Claude client: requests a tool, then answers. Verifies tool results and citations flow through."""
    from app import analyst
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    seen = {}

    class FakeMessages:
        def __init__(self):
            self.n = 0

        def create(self, **kw):
            self.n += 1
            seen.setdefault("calls", []).append(kw)
            assert kw["fallbacks"] == "default" and kw["tools"]
            if self.n == 1:
                return SimpleNamespace(stop_reason="tool_use", model="m", content=[
                    SimpleNamespace(type="tool_use", id="t1", name="get_market_overview", input={"symbol": "^GSPC"}),
                    SimpleNamespace(type="tool_use", id="t2", name="get_signal_statistics", input={"symbol": "^GSPC", "signal": "nope"}),
                ])
            results = kw["messages"][-1]["content"]
            seen["results"] = results
            return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text="Grounded answer [overview ^GSPC]")])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setattr(analyst.anthropic, "Anthropic", lambda **kw: fake)
    out = analyst.ask(db, "How close is the S&P to a bottom?")
    assert out["answer"].startswith("Grounded answer")
    assert out["citations"][0]["symbol"] == "^GSPC" and out["citations"][0]["as_of"] == "2018-12-31"
    res = seen["results"]
    assert len(res) == 2 and res[1]["is_error"] is True  # bad tool input reported to the model, not swallowed
    assert '"scores"' in res[0]["content"]


@pytest.mark.parametrize("tool,args", [
    ("analyze_symbol", {"symbol": "^IXIC"}), ("get_signal_scoreboard", {"symbol": "^GSPC"}),
    ("get_correction_frequency", {"symbol": "^GSPC", "threshold_pct": 10, "horizon_days": 126}),
    ("get_historical_analogs", {"symbol": "^GSPC"}), ("run_scanner", {"direction": "bullish"}), ("get_data_status", {}),
])
def test_analyst_tools(db, tool, args):
    from app.analyst import run_tool

    data, cites = run_tool(db, tool, args)
    assert data and cites
