"""Browser WebSocket distribution. One upstream subscription per symbol is shared by every connected client
(reference-counted in the hub). Updates are coalesced per symbol and sent at most every 250 ms, so trade
processing frequency and render frequency stay independent. Nothing about a client outlives its connection."""

import asyncio
import itertools
import logging
import re

from fastapi import WebSocket, WebSocketDisconnect

from .alerts import KINDS, SessionAlerts
from .config import get_settings
from .market import calendar as cal
from .market import licensing
from .market.hub import hub

log = logging.getLogger(__name__)
SYMBOL = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-]{0,11}$")
_ids = itertools.count(1)


class Client:
    def __init__(self, ws: WebSocket):
        self.id = next(_ids)
        self.ws = ws
        self.symbols: set[str] = set()
        self.alerts = SessionAlerts()
        self.outbox: list[dict] = []
        self.dead = False

    async def send(self, items: list[dict]) -> None:
        if self.dead or not items:
            return
        try:
            await asyncio.wait_for(self.ws.send_json({"type": "batch", "items": items}), 3)
        except Exception:
            self.dead = True  # slow or gone: the reader loop cleans up


clients: set[Client] = set()


def _initial(sym: str) -> list[dict]:
    return [{"type": "quote", "symbol": sym, "data": hub.view(sym)}, {"type": "bars", "symbol": sym, "bars": hub.bars(sym)}]


async def endpoint(ws: WebSocket) -> None:
    await ws.accept()
    c = Client(ws)
    clients.add(c)
    st = get_settings()
    await ws.send_json({"type": "hello", "session": cal.info(), "licensing": licensing.describe(), "max_symbols": st.max_symbols_per_client,
                        "alert_kinds": KINDS})
    try:
        while not c.dead:
            msg = await ws.receive_json()
            op = msg.get("op")
            if op == "subscribe":
                want = [s for s in (str(x).upper() for x in msg.get("symbols", [])) if SYMBOL.match(s)]
                for s in want:
                    if s in c.symbols:
                        continue
                    if len(c.symbols) >= st.max_symbols_per_client:
                        c.outbox.append({"type": "error", "op": "subscribe", "message": f"Limit of {st.max_symbols_per_client} live symbols per connection."})
                        break
                    c.symbols.add(s)
                    await hub.acquire(s)
                    c.outbox += _initial(s)
            elif op == "unsubscribe":
                for s in [str(x).upper() for x in msg.get("symbols", [])]:
                    if s in c.symbols:
                        c.symbols.discard(s)
                        await hub.release(s)
            elif op == "alerts":
                try:
                    rules = c.alerts.set_rules(list(msg.get("rules") or []))
                    c.outbox.append({"type": "alerts_ack", "rules": rules})
                except ValueError as e:
                    c.outbox.append({"type": "error", "op": "alerts", "message": str(e)})
            elif op == "ping":
                c.outbox.append({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        log.info("ws client %s closed: %s", c.id, e)
    finally:
        c.dead = True
        clients.discard(c)
        for s in c.symbols:
            await hub.release(s)


async def fanout_loop() -> None:
    tick = 0
    last_session = None
    while True:
        await asyncio.sleep(0.25)
        tick += 1
        dirty, bar_dirty = hub.take_dirty()
        resend_all = tick % 20 == 0  # every 5 s: refresh status so STALE/CLOSED transitions show without new events
        session = str(cal.session_at()) if tick % 4 == 0 else last_session
        for c in list(clients):
            items = c.outbox
            c.outbox = []
            for s in c.symbols:
                if s in dirty or resend_all:
                    items.append({"type": "quote", "symbol": s, "data": hub.view(s)})
                st = hub.states.get(s)
                for start in sorted(bar_dirty.get(s, ())):
                    b = st.bars.get(start) if st else None
                    if b is not None:
                        items.append({"type": "bar", "symbol": s, "bar": b.to_json()})
            if session != last_session and last_session is not None:
                items.append({"type": "session", "session": cal.info()})
            await c.send(items)
        last_session = session


def push_scores(symbol: str, snap: dict) -> None:
    for c in clients:
        if symbol in c.symbols:
            c.outbox.append({"type": "scores", "symbol": symbol, "data": snap})


async def alerts_loop() -> None:
    while True:
        await asyncio.sleep(60)
        for c in [x for x in list(clients) if x.alerts.rules]:
            try:
                fired = await asyncio.to_thread(c.alerts.evaluate)
            except Exception as e:  # one bad evaluation must not stop the loop
                log.warning("alert evaluation failed: %s", e)
                continue
            if fired:
                c.outbox.append({"type": "alert", "events": fired})
