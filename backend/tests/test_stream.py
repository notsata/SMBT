"""Streaming: provider message normalization, hub event rules, status classification, and reconnect/resubscribe
against a local fake upstream WebSocket server speaking each provider's documented protocol."""

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest
import websockets

from app.config import get_settings
from app.market import calendar as cal
from app.market.hub import Hub, SymbolState
from app.market.model import Bar, Kind, Status, Trade, now_ns
from app.market.stream import AlpacaStream, MassiveStream

NS = 10**9
T0 = int(datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc).timestamp()) * NS  # Fri 10:00 ET, regular session


def collect():
    out = {"t": [], "q": [], "b": [], "i": []}
    return out, dict(on_trade=out["t"].append, on_quote=out["q"].append, on_bar=lambda s, b: out["b"].append((s, b)),
                     on_index=lambda *a: out["i"].append(a))


def test_alpaca_message_normalization():
    out, cb = collect()
    s = AlpacaStream(**cb)
    s.handle({"T": "t", "S": "SPY", "i": 96921, "x": "V", "p": 601.25, "s": 100, "t": "2026-11-20T15:00:00.123456789Z", "c": ["@"], "z": "B"})
    s.handle({"T": "q", "S": "SPY", "bx": "V", "bp": 601.2, "bs": 3, "ax": "V", "ap": 601.3, "as": 4, "t": "2026-11-20T15:00:00.2Z", "c": ["R"], "z": "B"})
    s.handle({"T": "b", "S": "SPY", "o": 600, "h": 602, "l": 599, "c": 601, "v": 1000, "t": "2026-11-20T15:00:00Z", "n": 12, "vw": 600.8})
    t = out["t"][0]
    assert (t.symbol, t.price, t.size, t.exchange, t.id, t.provider) == ("SPY", 601.25, 100, "V", "96921", "alpaca")
    assert t.ts == T0 + 123456789
    assert out["q"][0].bid == 601.2 and out["q"][0].ask == 601.3
    sym, bar = out["b"][0]
    assert sym == "SPY" and bar.final and bar.start == T0 and bar.source.endswith(":stream")


def test_massive_message_normalization():
    out, cb = collect()
    s = MassiveStream(**cb)
    ms = T0 // 10**6
    s.handle({"ev": "T", "sym": "QQQ", "x": 4, "i": "abc", "z": 3, "p": 500.5, "s": 10, "c": [12], "t": ms, "q": 1})
    s.handle({"ev": "Q", "sym": "QQQ", "bx": 4, "bp": 500.4, "bs": 1, "ax": 7, "ap": 500.6, "as": 2, "t": ms})
    s.handle({"ev": "AM", "sym": "QQQ", "v": 10, "o": 500, "c": 500.5, "h": 501, "l": 499, "vw": 500.2, "s": ms, "e": ms + 60000, "z": 3})
    assert out["t"][0].ts == T0 and out["t"][0].exchange == "4" and out["q"][0].ask == 500.6 and out["b"][0][1].start == T0
    assert s.kind == Kind.DELAYED and s.url.endswith("delayed.massive.com/stocks")  # no real-time entitlement configured


def make_hub(kind=Kind.REALTIME):
    h = Hub()
    st = h.states["SPY"] = SymbolState("SPY", provider="alpaca", feed="iex", kind=kind, delay_s=0 if kind == Kind.REALTIME else 900)
    st.seeded = True
    return h, st


def tr(ts, price, size=100, tid="1", ex="V", conds=(), provider="alpaca"):
    return Trade("SPY", price, size, ts, ex, tid, tuple(conds), provider, "iex", now_ns())


def test_dedup_ordering_conditions_and_vwap():
    h, st = make_hub()
    h.on_trade(tr(T0 + 2 * NS, 101, tid="2"))
    h.on_trade(tr(T0 + 2 * NS, 101, tid="2"))  # duplicate delivery
    h.on_trade(tr(T0 + 1 * NS, 100, tid="1"))  # arrives late, older timestamp
    h.on_trade(tr(T0 + 3 * NS, 150, size=5, tid="3", conds=("I",)))  # odd lot: volume only
    h.on_trade(tr(T0 + 4 * NS, 101, tid="4", provider="massive"))  # a provider this symbol is not bound to
    assert st.duplicates == 1 and st.out_of_order == 1
    assert st.last == 101 and st.day_high == 101 and st.day_low == 100  # odd lot did not set price or range
    assert st.day_volume == 205 and st.vwap() == pytest.approx((101 * 100 + 100 * 100) / 200)
    assert st.events == 3  # duplicate not counted; the massive print was ignored entirely


def test_seed_prevents_double_counting_and_day_roll():
    h, st = make_hub()
    st.seed_ts, st.day_volume, st.session_date = T0 + 10 * NS, 1_000_000, datetime(2026, 11, 20).date()
    h.on_trade(tr(T0 + 5 * NS, 100, tid="a"))  # already inside the snapshot volume
    h.on_trade(tr(T0 + 11 * NS, 100, tid="b"))
    assert st.day_volume == 1_000_100
    h.on_trade(tr(T0 + 20 * NS, 102, tid="c"))
    nxt = int(datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc).timestamp()) * NS  # Monday
    h.on_trade(tr(nxt, 103, tid="d"))
    assert st.prev_close == 102 and st.day_volume == 100 and st.day_open == 103


def test_minute_bars_provisional_then_official_and_late_trades():
    h, st = make_hub()
    h.on_trade(tr(T0 + 1 * NS, 100, tid="1"))
    h.on_trade(tr(T0 + 30 * NS, 102, tid="2"))
    h.on_trade(tr(T0 + 61 * NS, 101, tid="3"))
    b0 = st.bars[T0]
    assert (b0.open, b0.high, b0.close, b0.final) == (100, 102, 102, False)
    h.on_bar("SPY", Bar(T0, 100, 102.5, 99.9, 102.1, 5000, 101, 50, True, "alpaca:iex:stream"))
    assert st.bars[T0].high == 102.5 and st.bars[T0].final
    h.on_trade(tr(T0 + 2 * NS, 99, tid="4"))  # late trade for a minute the provider already finalized
    assert st.bars[T0].low == 99.9
    h.on_trade(tr(T0 + 20 * 60 * NS, 104, tid="5"))
    h.on_trade(tr(T0 + 3 * 60 * NS, 98, tid="6"))  # > 5 minutes behind the newest bar: counted as late
    assert st.late == 1 and list(st.bars) == sorted(st.bars)


def test_status_classification(monkeypatch):
    h, st = make_hub()

    class Up:
        name, healthy, state, error = "alpaca", True, "streaming", None

    h.active = Up()
    st.last, st.last_recv = 100.0, now_ns()
    monkeypatch.setattr(cal, "session_at", lambda ts=None: cal.Session.REGULAR)
    assert h.status(st)[0] == Status.LIVE
    st.kind = Kind.DELAYED
    assert h.status(st)[0] == Status.DELAYED
    st.kind, st.last_recv = Kind.REALTIME, now_ns() - 3600 * NS
    assert h.status(st)[0] == Status.STALE
    st.last_recv = now_ns()
    Up.healthy, Up.state = False, "reconnecting"
    assert h.status(st)[0] == Status.STALE
    monkeypatch.setattr(cal, "session_at", lambda ts=None: cal.Session.CLOSED)
    assert h.status(st)[0] == Status.CLOSED
    monkeypatch.setattr(get_settings(), "access_mode", "public")
    assert h.status(st)[0] == Status.UNAVAILABLE  # alpaca not licensed for public display
    v = h.view("SPY")
    assert v["status"] == "UNAVAILABLE" and "last" not in v


async def _fake_alpaca(behaviour: str, received: list, port_holder: list):
    async def handler(ws):
        await ws.send(json.dumps([{"T": "success", "msg": "connected"}]))
        auth = json.loads(await ws.recv())
        received.append(auth)
        if behaviour == "badauth":
            await ws.send(json.dumps([{"T": "error", "code": 402, "msg": "auth failed"}]))
            await ws.close()
            return
        await ws.send(json.dumps([{"T": "success", "msg": "authenticated"}]))
        sub = json.loads(await ws.recv())
        received.append(sub)
        await ws.send(json.dumps([{"T": "subscription", "trades": sub["trades"], "quotes": sub["quotes"], "bars": sub["bars"]}]))
        n = sum(1 for m in received if m.get("action") == "subscribe")
        await ws.send(json.dumps([{"T": "t", "S": "SPY", "i": n, "x": "V", "p": 600 + n, "s": 1, "t": "2026-11-20T15:00:0%dZ" % n}]))
        await asyncio.sleep(0.2)
        await ws.close()  # drop the connection: the client must reconnect and resubscribe

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port_holder.append(server.sockets[0].getsockname()[1])
    return server


@pytest.mark.parametrize("behaviour", ["drop", "badauth"])
def test_alpaca_stream_reconnects_and_resubscribes(monkeypatch, behaviour):
    st = get_settings()
    monkeypatch.setattr(st, "alpaca_api_key_id", "id")
    monkeypatch.setattr(st, "alpaca_api_secret_key", "secret")

    async def scenario():
        received, port = [], []
        server = await _fake_alpaca(behaviour, received, port)
        monkeypatch.setattr(st, "alpaca_stream_url", f"ws://127.0.0.1:{port[0]}/v2")
        out, cb = collect()
        s = AlpacaStream(**cb)
        await s.set_symbols({"SPY"})
        s.start()
        deadline = time.time() + 8
        while time.time() < deadline and len(out["t"]) < 2 and s.state != "error":
            await asyncio.sleep(0.1)
        state, error, reconnects = s.state, s.error, s.reconnects
        await s.stop()
        server.close()
        return received, out, state, error, reconnects

    received, out, state, error, reconnects = asyncio.run(scenario())
    assert received[0] == {"action": "auth", "key": "id", "secret": "secret"}
    if behaviour == "badauth":
        assert state == "error" and "402" in error and not out["t"]
    else:
        subs = [m for m in received if m.get("action") == "subscribe"]
        assert len(subs) >= 2 and all(m["trades"] == ["SPY"] for m in subs)  # resubscribed after the drop
        assert [t.price for t in out["t"][:2]] == [601, 602] and reconnects >= 1
