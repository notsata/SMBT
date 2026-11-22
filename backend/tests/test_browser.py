"""Real browser, real server: fake Alpaca upstream -> hub -> uvicorn /ws -> built PWA in Chromium (frontend/tests/browser.e2e.mjs).
Skipped unless the frontend has been built (frontend/out) and Node can load Playwright."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.sync.server import serve

from app.config import get_settings
from app.market.http import Http, set_http

from .test_e2e_live import fake_rest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
SCRIPT = FRONTEND / "tests" / "browser.e2e.mjs"


def _node_has_playwright() -> bool:
    node = shutil.which("node")
    return bool(node) and subprocess.run([node, "-e", "require('playwright')"], cwd=FRONTEND, capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not (FRONTEND / "out" / "index.html").exists() or not _node_has_playwright(),
                                reason="needs a built frontend (npm run build) and Node Playwright")


def streaming_upstream(ws):
    """Alpaca-shaped feed that keeps printing SPY trades (one id per trade, timestamps strictly increasing)."""
    ws.send(json.dumps([{"T": "success", "msg": "connected"}]))
    json.loads(ws.recv())
    ws.send(json.dumps([{"T": "success", "msg": "authenticated"}]))
    t0 = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    i = 0
    try:
        while i < 2000:
            ws.socket.settimeout(0.25)
            try:
                sub = json.loads(ws.recv(timeout=0.25))
                if sub.get("action") == "subscribe":
                    ws.send(json.dumps([{"T": "subscription", "trades": sub.get("trades", []), "quotes": sub.get("quotes", []), "bars": sub.get("bars", [])}]))
            except TimeoutError:
                pass
            i += 1
            ts = (t0 + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            ws.send(json.dumps([{"T": "t", "S": "SPY", "i": 5000 + i, "x": "V", "p": round(600 + i / 100, 2), "s": 10, "t": ts, "c": ["@"]}]))
    except Exception:
        return


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_pwa_in_real_browser(monkeypatch):
    st = get_settings()
    up = serve(streaming_upstream, "127.0.0.1", 0)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    for k, v in {"alpaca_api_key_id": "kid", "alpaca_api_secret_key": "ksecret", "alpaca_feed": "iex",
                 "alpaca_stream_url": f"ws://127.0.0.1:{up.socket.getsockname()[1]}/v2", "alpaca_data_url": "http://alpaca.test"}.items():
        monkeypatch.setattr(st, k, v)
    set_http(Http(0, client=httpx.Client(transport=httpx.MockTransport(fake_rest))))
    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.1)
        assert server.started
        if httpx.get(f"http://127.0.0.1:{port}/").status_code != 200:
            pytest.skip("frontend/out was built after the app was imported in this session")
        r = subprocess.run(["node", str(SCRIPT), f"http://127.0.0.1:{port}"], cwd=FRONTEND, capture_output=True, text=True, timeout=240,
                           env=os.environ | ({"SCREENSHOT_DIR": d} if (d := os.environ.get("SCREENSHOT_DIR")) else {}))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ok desktop" in r.stdout and "ok iphone" in r.stdout
    finally:
        server.should_exit = True
        th.join(10)
        set_http(None)
        up.shutdown()
