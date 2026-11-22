"""Upstream WebSocket clients. Each keeps one connection, authenticates, (re)subscribes the requested symbols,
parses provider messages into normalized Trade/Quote/Bar events and reconnects with exponential backoff.
A watchdog forces a reconnect when an authenticated, subscribed connection goes silent during market hours."""

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable

import websockets

from ..config import get_settings
from . import calendar as cal
from .model import Bar, Kind, Quote, Trade, now_ns, parse_ts

log = logging.getLogger(__name__)


class Fatal(Exception):
    """Credentials or entitlement problem: retrying immediately would not help."""


class Upstream:
    name = "base"
    max_symbols = 10_000

    def __init__(self, on_trade: Callable, on_quote: Callable, on_bar: Callable, on_index: Callable | None = None):
        self.on_trade, self.on_quote, self.on_bar, self.on_index = on_trade, on_quote, on_bar, on_index
        self.wanted: set[str] = set()
        self.subscribed: set[str] = set()
        self.ws = None
        self.state = "idle"
        self.error: str | None = None
        self.connected_at: float | None = None
        self.last_msg: float | None = None
        self.messages = 0
        self.reconnects = 0
        self._task: asyncio.Task | None = None
        self._stop = False

    # -- provider specifics ---------------------------------------------------------------------
    url: str = ""
    feed: str = ""
    kind: Kind = Kind.REALTIME
    delay_s: int = 0

    async def authenticate(self, ws) -> None: ...
    def sub_msg(self, symbols: set[str], subscribe: bool) -> dict: ...
    def handle(self, msg: dict) -> None: ...

    # -- lifecycle ------------------------------------------------------------------------------
    @property
    def healthy(self) -> bool:
        return self.state == "streaming"

    def start(self) -> None:
        if self._task is None:
            self._stop = False
            self._task = asyncio.create_task(self._run(), name=f"upstream-{self.name}")

    async def stop(self) -> None:
        self._stop = True
        if self.ws is not None:
            await self.ws.close()
        if self._task:
            self._task.cancel()

    async def set_symbols(self, symbols: set[str]) -> None:
        self.wanted = set(sorted(symbols)[: self.max_symbols])
        if self.ws is not None and self.state == "streaming":
            await self._sync()

    async def _sync(self) -> None:
        add, drop = self.wanted - self.subscribed, self.subscribed - self.wanted
        if drop:
            await self.ws.send(json.dumps(self.sub_msg(drop, False)))
            self.subscribed -= drop
        if add:
            await self.ws.send(json.dumps(self.sub_msg(add, True)))
            self.subscribed |= add

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            self.state = "connecting"
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20, max_size=2**24, open_timeout=15) as ws:
                    self.ws = ws
                    await self.authenticate(ws)
                    self.state, self.error, self.connected_at = "streaming", None, time.time()
                    self.subscribed = set()
                    await self._sync()
                    backoff = 1.0
                    watchdog = asyncio.create_task(self._watchdog(ws))
                    try:
                        async for raw in ws:
                            self.last_msg = time.time()
                            self.messages += 1
                            for m in json.loads(raw) if raw[:1] in ("[", b"[") else [json.loads(raw)]:
                                self.handle(m)
                    finally:
                        watchdog.cancel()
            except Fatal as e:
                self.state, self.error = "error", str(e)
                log.error("%s stream: %s (retrying in 5 min)", self.name, e)
                await asyncio.sleep(300)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors, protocol errors, server closes
                self.error = f"{type(e).__name__}: {e}"[:300]
                log.warning("%s stream disconnected: %s", self.name, self.error)
            finally:
                self.ws = None
                if self.state != "error":
                    self.state = "reconnecting"
            self.reconnects += 1
            await asyncio.sleep(backoff + random.random())
            backoff = min(backoff * 2, 60)

    async def _watchdog(self, ws) -> None:
        st = get_settings()
        while True:
            await asyncio.sleep(10)
            s = cal.session_at()
            limit = st.stale_regular_s if s == cal.Session.REGULAR else st.stale_extended_s if s != cal.Session.CLOSED else None
            if limit and self.subscribed and self.last_msg and time.time() - self.last_msg > limit:
                log.warning("%s stream silent for %ds during %s session; reconnecting", self.name, time.time() - self.last_msg, s)
                await ws.close(code=4000, reason="silent feed")
                return


class AlpacaStream(Upstream):
    """wss://stream.data.alpaca.markets/v2/{iex|sip|delayed_sip}; trades, quotes, minute bars."""

    name = "alpaca"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        st = get_settings()
        self.feed = st.alpaca_feed
        self.url = f"{st.alpaca_stream_url}/{self.feed}"
        self.kind = Kind.DELAYED if self.feed == "delayed_sip" else Kind.REALTIME
        self.delay_s = 900 if self.feed == "delayed_sip" else 0
        self.max_symbols = 30 if self.feed == "iex" else 10_000  # Basic plan: 30 symbols

    async def authenticate(self, ws) -> None:
        st = get_settings()
        await ws.send(json.dumps({"action": "auth", "key": st.alpaca_api_key_id, "secret": st.alpaca_api_secret_key}))
        deadline = time.time() + 15
        while time.time() < deadline:
            for m in json.loads(await asyncio.wait_for(ws.recv(), 15)):
                if m.get("T") == "success" and m.get("msg") == "authenticated":
                    return
                if m.get("T") == "error":
                    code = m.get("code")
                    msg = {402: "authentication failed", 406: "connection limit exceeded (another process uses this account's stream)",
                           409: f"plan does not include the {self.feed} feed"}.get(code, m.get("msg", "error"))
                    raise Fatal(f"Alpaca {code}: {msg}")
        raise ConnectionError("Alpaca auth timeout")

    def sub_msg(self, symbols: set[str], subscribe: bool) -> dict:
        s = sorted(symbols)
        return {"action": "subscribe" if subscribe else "unsubscribe", "trades": s, "quotes": s, "bars": s}

    def handle(self, m: dict) -> None:
        t, recv = m.get("T"), now_ns()
        if t == "t":
            self.on_trade(Trade(m["S"], float(m["p"]), float(m["s"]), parse_ts(m["t"]), str(m.get("x", "")), str(m.get("i", "")),
                                tuple(m.get("c") or ()), self.name, self.feed, recv))
        elif t == "q":
            self.on_quote(Quote(m["S"], float(m["bp"]), float(m["bs"]), float(m["ap"]), float(m["as"]), parse_ts(m["t"]), self.name, self.feed, recv))
        elif t in ("b", "u"):  # minute bar / updated (late-trade corrected) minute bar
            self.on_bar(m["S"], Bar(parse_ts(m["t"]), m["o"], m["h"], m["l"], m["c"], m["v"], m.get("vw"), m.get("n", 0), True, f"{self.name}:{self.feed}:stream"))
        elif t == "error":
            self.error = f"{m.get('code')}: {m.get('msg')}"
            log.warning("alpaca stream error %s", self.error)


class MassiveStream(Upstream):
    """wss://socket.massive.com/stocks (real-time) or wss://delayed.massive.com/stocks (15-min delayed)."""

    name = "massive"
    cluster = "stocks"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        st = get_settings()
        self.kind = Kind.REALTIME if st.massive_realtime else Kind.DELAYED
        self.delay_s = 0 if st.massive_realtime else 900
        self.feed = "realtime" if st.massive_realtime else "delayed"
        self.url = f"{st.massive_ws_realtime_url if st.massive_realtime else st.massive_ws_delayed_url}/{self.cluster}"

    async def authenticate(self, ws) -> None:
        await ws.send(json.dumps({"action": "auth", "params": get_settings().massive_api_key}))
        deadline = time.time() + 15
        while time.time() < deadline:
            for m in json.loads(await asyncio.wait_for(ws.recv(), 15)):
                if m.get("ev") == "status" and m.get("status") == "auth_success":
                    return
                if m.get("ev") == "status" and m.get("status") in ("auth_failed", "max_connections", "error"):
                    raise Fatal(f"Massive: {m.get('message') or m.get('status')}")
        raise ConnectionError("Massive auth timeout")

    def sub_msg(self, symbols: set[str], subscribe: bool) -> dict:
        params = ",".join(f"{c}.{s}" for s in sorted(symbols) for c in ("T", "Q", "AM"))
        return {"action": "subscribe" if subscribe else "unsubscribe", "params": params}

    def handle(self, m: dict) -> None:
        ev, recv = m.get("ev"), now_ns()
        if ev == "T":
            self.on_trade(Trade(m["sym"], float(m["p"]), float(m.get("s", 0)), int(m["t"]) * 1_000_000, str(m.get("x", "")), str(m.get("i", "")),
                                tuple(m.get("c") or ()), self.name, self.feed, recv))
        elif ev == "Q":
            self.on_quote(Quote(m["sym"], float(m["bp"]), float(m.get("bs", 0)), float(m["ap"]), float(m.get("as", 0)), int(m["t"]) * 1_000_000,
                                self.name, self.feed, recv))
        elif ev == "AM":
            self.on_bar(m["sym"], Bar(int(m["s"]) * 1_000_000, m["o"], m["h"], m["l"], m["c"], m.get("v", 0), m.get("vw"), m.get("z", 0), True,
                                      f"{self.name}:{self.feed}:stream"))
        elif ev == "status" and m.get("status") not in ("connected", "auth_success", "success"):
            self.error = m.get("message")


class MassiveIndexStream(MassiveStream):
    """Indices cluster: index values (V.I:VIX) on the Massive Indices plan."""

    name = "massive-indices"
    cluster = "indices"

    def sub_msg(self, symbols: set[str], subscribe: bool) -> dict:
        return {"action": "subscribe" if subscribe else "unsubscribe", "params": ",".join(f"V.I:{s.lstrip('^')}" for s in sorted(symbols))}

    def handle(self, m: dict) -> None:
        if m.get("ev") == "V" and self.on_index:
            self.on_index("^" + m["T"][2:], float(m["val"]), int(m["t"]) * 1_000_000, self.feed)
