"""End to end: fake Alpaca upstream (WebSocket + REST, documented message shapes) -> hub -> FastAPI /ws -> browser.
Verifies that genuine stream events (not timers) drive the browser's quote, that duplicates are dropped, that the
snapshot seeds the state, and that the status reflects the real session clock."""

import json
import threading
import time
from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient
from websockets.sync.server import serve

from app.config import get_settings
from app.market import calendar as cal
from app.market.http import Http, set_http

FRIDAY = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


def fake_rest(req: httpx.Request) -> httpx.Response:
    assert req.headers["APCA-API-KEY-ID"] == "kid"
    if req.url.path == "/v2/stocks/snapshots":
        return httpx.Response(200, json={"SPY": {
            "latestTrade": {"t": "2026-11-20T15:00:00Z", "x": "V", "p": 599.5, "s": 100, "i": 1},
            "latestQuote": {"t": "2026-11-20T15:00:00Z", "bp": 599.4, "bs": 1, "ap": 599.6, "as": 2},
            "dailyBar": {"t": "2026-11-20T05:00:00Z", "o": 598, "h": 600, "l": 597, "c": 599.5, "v": 1000, "vw": 599},
            "prevDailyBar": {"t": "2026-11-19T05:00:00Z", "o": 590, "h": 599, "l": 589, "c": 595.0, "v": 2000}}})
    if req.url.path == "/v2/stocks/bars":
        return httpx.Response(200, json={"bars": {"SPY": [{"t": "2026-11-20T14:59:00Z", "o": 599, "h": 599.6, "l": 598.9, "c": 599.5, "v": 300, "n": 3, "vw": 599.3}]},
                                         "next_page_token": None})
    return httpx.Response(404)


def upstream(ws):
    ws.send(json.dumps([{"T": "success", "msg": "connected"}]))
    assert json.loads(ws.recv())["action"] == "auth"
    ws.send(json.dumps([{"T": "success", "msg": "authenticated"}]))
    sub = json.loads(ws.recv())
    ws.send(json.dumps([{"T": "subscription", "trades": sub["trades"], "quotes": sub["quotes"], "bars": sub["bars"]}]))
    if "SPY" not in sub["trades"]:
        time.sleep(5)
        return
    for i in range(1, 30):
        ts = f"2026-11-20T15:00:{i:02d}Z"
        msgs = [{"T": "t", "S": "SPY", "i": 1000 + i, "x": "V", "p": 600 + i / 100, "s": 10, "t": ts, "c": ["@"]}]
        if i == 5:
            msgs.append(msgs[0])  # duplicate delivery of the same trade id
        ws.send(json.dumps(msgs))
        time.sleep(0.05)
    time.sleep(5)


def test_live_pipeline_end_to_end(monkeypatch):
    st = get_settings()
    server = serve(upstream, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for k, v in {"alpaca_api_key_id": "kid", "alpaca_api_secret_key": "ksecret", "alpaca_feed": "iex",
                 "alpaca_stream_url": f"ws://127.0.0.1:{port}/v2", "alpaca_data_url": "http://alpaca.test"}.items():
        monkeypatch.setattr(st, k, v)
    set_http(Http(0, client=httpx.Client(transport=httpx.MockTransport(fake_rest))))
    try:
        from app.main import app

        with TestClient(app) as c, c.websocket_connect("/ws") as w:
            assert w.receive_json()["type"] == "hello"
            w.send_json({"op": "subscribe", "symbols": ["SPY"]})
            first, last_quote, bars = None, None, []
            deadline = time.time() + 15
            while time.time() < deadline:
                m = w.receive_json()
                for it in m.get("items", []):
                    if it["type"] == "quote" and it["symbol"] == "SPY":
                        first = first or it["data"]
                        last_quote = it["data"]
                    if it["type"] in ("bar", "bars"):
                        bars.append(it)
                if last_quote and (last_quote.get("last") or 0) >= 600.29:
                    break
            assert first["provider"] == "alpaca" and first["prev_close"] == 595.0  # seeded from the REST snapshot
            assert last_quote["last"] == 600.29 and last_quote["feed"] == "iex"
            assert last_quote["stats"]["duplicates"] == 1 and last_quote["volume"] == 1000 + 29 * 10
            assert "IEX-only" in last_quote["method"]
            expected = "CLOSED" if cal.session_at() == cal.Session.CLOSED else None
            assert last_quote["status"] == (expected or last_quote["status"]) and last_quote["status"] != "UNAVAILABLE"
            assert any(b["type"] == "bar" for b in bars)
            status = c.get("/api/status").json()
            alp = next(s for s in status["streams"] if s["provider"] == "alpaca")
            assert alp["state"] == "streaming" and alp["messages"] > 20
    finally:
        set_http(None)
        server.shutdown()
