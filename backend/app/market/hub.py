"""In-memory market data engine: live per-symbol state from one upstream provider at a time, reference-counted
shared subscriptions, status classification (LIVE/DELAYED/STALE/CLOSED/UNAVAILABLE) and change notification.

Event handling rules
- Dedup: (exchange, trade id) per symbol, bounded memory.
- Ordering: an older trade never replaces a newer last price; it still counts toward volume, VWAP, high/low and
  its own minute bar.
- Session day stats (open/high/low/VWAP) use regular-session trades; volume counts all sessions. Stats are seeded
  from the provider snapshot at subscribe time and only trades after the snapshot are added (no double counting).
- Minute bars built from trades are provisional; the provider's official minute bar replaces them when it arrives.
- One provider per symbol: a failover reseeds the state from the new provider instead of mixing feeds.
"""

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..config import get_settings
from . import calendar as cal
from . import licensing
from .history import intraday
from .http import ProviderError
from .model import Bar, Kind, Quote, Status, Trade, iso, now_ns
from .providers import alpaca, massive
from .stream import AlpacaStream, MassiveIndexStream, MassiveStream, Upstream

log = logging.getLogger(__name__)
NS = 1_000_000_000
MIN = 60 * NS

# Sale conditions that must not set last price / high / low (CTA & UTP letters as sent by Alpaca):
# odd lot, average price, out of sequence, derivatively priced, QCT, cash, next day, seller, prior reference,
# official open/close reports, corrected close.
NO_LAST = {"I", "W", "Z", "U", "4", "7", "C", "N", "R", "P", "M", "Q", "9", "O", "6"}
NO_VOLUME = {"M", "Q", "9", "O", "6"}  # summary/official-price messages, not trades


@dataclass
class SymbolState:
    symbol: str
    provider: str = ""
    feed: str = ""
    kind: Kind = Kind.REALTIME
    delay_s: int = 0
    last: float | None = None
    last_ts: int | None = None
    last_recv: int | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    quote_ts: int | None = None
    session_date: date | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    day_volume: float = 0.0
    pv: float = 0.0  # sum(price * size) for regular-session VWAP
    pv_vol: float = 0.0
    prev_close: float | None = None
    reg_last: float | None = None  # last regular-session price (becomes prev_close when the day rolls)
    ext_last: float | None = None
    ext_ts: int | None = None
    seed_ts: int = 0  # trades at or before this time are already inside the seeded snapshot stats
    seeded: bool = False
    seed_error: str | None = None
    bars: "OrderedDict[int, Bar]" = field(default_factory=OrderedDict)
    bar_ts: dict = field(default_factory=dict)  # minute start -> timestamp of the trade that set its close
    seen: set = field(default_factory=set)
    seen_q: deque = field(default_factory=lambda: deque(maxlen=20000))
    events: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    late: int = 0
    is_index: bool = False

    def vwap(self) -> float | None:
        return self.pv / self.pv_vol if self.pv_vol > 0 else None


class Bounds:
    """Per-day session boundaries in ns, cached so per-trade classification is just comparisons."""

    def __init__(self):
        self._cache: dict[date, tuple[int, int, int, int] | None] = {}

    def get(self, d: date):
        if d not in self._cache:
            if cal.is_trading_day(d):
                o, c = cal.open_close(d)
                o_ns, c_ns = int(o.timestamp() * NS), int(c.timestamp() * NS)
                self._cache[d] = (o_ns - int(5.5 * 3600 * NS), o_ns, c_ns, c_ns + 4 * 3600 * NS)
            else:
                self._cache[d] = None
        return self._cache[d]

    def classify(self, ts_ns: int) -> tuple[date, cal.Session]:
        d = datetime.fromtimestamp(ts_ns / NS, timezone.utc).astimezone(cal.ET).date()
        b = self.get(d)
        if b is None:
            return d, cal.Session.CLOSED
        pre, o, c, post = b
        s = cal.Session.PRE if pre <= ts_ns < o else cal.Session.REGULAR if o <= ts_ns < c else cal.Session.POST if c <= ts_ns < post else cal.Session.CLOSED
        return d, s


class Hub:
    def __init__(self):
        self.states: dict[str, SymbolState] = {}
        self.refs: dict[str, int] = {}
        self.released: dict[str, float] = {}
        self.pinned: set[str] = set()
        self.dirty: set[str] = set()
        self.bar_dirty: dict[str, set[int]] = {}
        self.bounds = Bounds()
        self.streams: list[Upstream] = []
        self.index_stream: MassiveIndexStream | None = None
        self.active: Upstream | None = None
        self._down_since: float | None = None
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    # -- setup ----------------------------------------------------------------------------------
    def configure(self) -> None:
        st = get_settings()
        cb = dict(on_trade=self.on_trade, on_quote=self.on_quote, on_bar=self.on_bar, on_index=self.on_index)
        for name in st.stream_order:
            if name == "alpaca" and st.has_alpaca:
                self.streams.append(AlpacaStream(**cb))
            elif name == "massive" and st.massive_api_key:
                self.streams.append(MassiveStream(**cb))
        if st.massive_api_key and st.massive_indices:
            self.index_stream = MassiveIndexStream(**cb)
        self.active = self.streams[0] if self.streams else None

    async def start(self, pinned: list[str]) -> None:
        self.__init__()  # a (re)start begins from empty memory: no state survives a restart
        self.configure()
        if self.active:
            self.active.start()
        if self.index_stream:
            self.index_stream.start()
        self._tasks = [asyncio.create_task(self._maintain(), name="hub-maintain")]
        for s in pinned:  # benchmarks stay subscribed so the first visitor sees a warm feed
            self.pinned.add(s)
            await self.acquire(s)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for s in [*self.streams, self.index_stream]:
            if s:
                await s.stop()

    # -- subscriptions --------------------------------------------------------------------------
    async def acquire(self, symbol: str) -> SymbolState:
        symbol = symbol.upper()
        self.refs[symbol] = self.refs.get(symbol, 0) + 1
        self.released.pop(symbol, None)
        st = self.states.get(symbol)
        if st is None:
            st = self.states[symbol] = SymbolState(symbol, is_index=symbol.startswith("^"))
            await self._attach(st)
        return st

    async def release(self, symbol: str) -> None:
        symbol = symbol.upper()
        self.refs[symbol] = max(0, self.refs.get(symbol, 0) - 1)
        if self.refs[symbol] == 0 and symbol not in self.pinned:
            self.released[symbol] = time.time()  # dropped after a grace period, so tab switches don't churn upstream

    def _stream_for(self, st: SymbolState) -> Upstream | None:
        return self.index_stream if st.is_index else self.active

    async def _attach(self, st: SymbolState) -> None:
        up = self._stream_for(st)
        if up is None:
            st.seed_error = "No streaming provider configured for indexes (needs Massive Indices)." if st.is_index else "No streaming provider configured."
            return
        st.provider, st.feed, st.kind, st.delay_s = up.name.split("-")[0], up.feed, up.kind, up.delay_s
        await up.set_symbols({s for s, x in self.states.items() if x.is_index == st.is_index and (self.refs.get(s, 0) > 0 or s in self.pinned)})
        if not st.is_index:
            now = datetime.now(timezone.utc)
            today = cal.current_session_date(now) or cal.last_completed_session(now)
            snap, bars, err = await asyncio.to_thread(self._fetch_seed, st.symbol, st.provider, today)
            self._apply_seed(st, snap, bars, err, today)  # applied on the event loop: no races with live events

    @staticmethod
    def _fetch_seed(symbol: str, provider: str, today: date):
        """REST snapshot + today's minute bars from the same provider as the stream (runs in a worker thread)."""
        try:
            snap = (alpaca.snapshots if provider == "alpaca" else massive.snapshots)([symbol]).get(symbol)
        except ProviderError as e:
            return None, [], str(e)
        try:
            bars = intraday(symbol, provider, today)
        except ProviderError as e:
            return snap, [], f"minute history: {e}"
        return snap, bars, None if snap else f"{provider} returned no snapshot for {symbol}"

    def _apply_seed(self, st: SymbolState, snap: dict | None, bars: list, err: str | None, today: date) -> None:
        st.seed_error = err
        if not snap:
            return
        day_ts = snap.get("day_ts")
        day_is_today = day_ts is not None and self.bounds.classify(day_ts)[0] == today
        st.session_date = today
        st.prev_close = snap.get("prev_close") if day_is_today else snap.get("day_close") or snap.get("prev_close")
        if day_is_today:
            st.day_open, st.day_high, st.day_low = snap.get("day_open"), snap.get("day_high"), snap.get("day_low")
            st.day_volume = float(snap.get("day_volume") or 0)
            if snap.get("day_vwap") and st.day_volume:
                st.pv, st.pv_vol = snap["day_vwap"] * st.day_volume, st.day_volume
        if snap.get("last") is not None and (st.last_ts is None or (snap.get("last_ts") or 0) > st.last_ts):
            st.last, st.last_ts, st.last_recv = snap["last"], snap.get("last_ts"), now_ns()
        if snap.get("bid") is not None and (st.quote_ts is None or (snap.get("quote_ts") or 0) > st.quote_ts):
            st.bid, st.ask, st.bid_size, st.ask_size, st.quote_ts = snap["bid"], snap["ask"], snap.get("bid_size"), snap.get("ask_size"), snap.get("quote_ts")
        st.seed_ts = max(st.seed_ts, snap.get("last_ts") or 0)
        for b in bars:
            st.bars.setdefault(b.start, b)
        st.bars = OrderedDict(sorted(st.bars.items())[-1200:])
        st.seeded = True
        self.dirty.add(st.symbol)

    # -- events (called from the event loop by the upstream readers) ---------------------------
    def _state(self, symbol: str, provider: str) -> SymbolState | None:
        st = self.states.get(symbol)
        return st if st is not None and st.provider == provider else None  # ignore a provider we are not bound to

    def _roll_day(self, st: SymbolState, d: date) -> None:
        if st.session_date != d:
            if st.session_date is not None and st.reg_last is not None and st.session_date < d:
                st.prev_close = st.reg_last
            st.session_date, st.day_open, st.day_high, st.day_low = d, None, None, None
            st.day_volume = st.pv = st.pv_vol = 0.0
            st.bars.clear()
            st.bar_ts.clear()

    def on_trade(self, t: Trade) -> None:
        st = self._state(t.symbol, t.provider)
        if st is None:
            return
        key = (t.exchange, t.id)
        if t.id and key in st.seen:
            st.duplicates += 1
            return
        if t.id:
            if len(st.seen_q) == st.seen_q.maxlen:
                st.seen.discard(st.seen_q[0])
            st.seen_q.append(key)
            st.seen.add(key)
        st.events += 1
        d, sess = self.bounds.classify(t.ts)
        if sess == cal.Session.CLOSED:
            return
        if st.session_date is not None and d < st.session_date:
            st.late += 1
            return
        self._roll_day(st, d)
        conds = set(t.conditions) if t.provider == "alpaca" else set()
        counts_volume = not (conds & NO_VOLUME)
        sets_price = not (conds & NO_LAST)
        after_seed = t.ts > st.seed_ts
        if after_seed and counts_volume:
            st.day_volume += t.size
        if sess == cal.Session.REGULAR and sets_price and after_seed:
            st.day_open = st.day_open if st.day_open is not None else t.price
            st.day_high = t.price if st.day_high is None else max(st.day_high, t.price)
            st.day_low = t.price if st.day_low is None else min(st.day_low, t.price)
            if counts_volume:
                st.pv += t.price * t.size
                st.pv_vol += t.size
        if sets_price:
            if sess == cal.Session.REGULAR:
                st.reg_last = t.price if st.last_ts is None or t.ts >= st.last_ts else st.reg_last
            if st.last_ts is None or t.ts >= st.last_ts:
                st.last, st.last_ts, st.last_recv = t.price, t.ts, t.recv
                if sess != cal.Session.REGULAR:
                    st.ext_last, st.ext_ts = t.price, t.ts
            else:
                st.out_of_order += 1
            self._bar_trade(st, t, counts_volume)
        self.dirty.add(st.symbol)

    def _bar_trade(self, st: SymbolState, t: Trade, counts_volume: bool) -> None:
        start = t.ts - t.ts % MIN
        b = st.bars.get(start)
        if b is None:
            if st.bars and start < next(reversed(st.bars)) - 5 * MIN:
                st.late += 1  # too late to rebuild a closed bar; the provider's bar is authoritative
                return
            b = st.bars[start] = Bar(start, t.price, t.price, t.price, t.price, 0.0, None, 0, False, f"{t.provider}:{t.feed}:trades")
            if len(st.bars) > 1500:
                st.bars.popitem(last=False)
        elif b.final and b.source.endswith(":stream"):
            return  # the provider's official bar already covers this minute
        b.high, b.low = max(b.high, t.price), min(b.low, t.price)
        if t.ts >= st.bar_ts.get(start, 0):
            b.close = t.price
            st.bar_ts[start] = t.ts
        if counts_volume:
            b.volume += t.size
        b.trades += 1
        self.bar_dirty.setdefault(st.symbol, set()).add(start)

    def on_quote(self, q: Quote) -> None:
        st = self._state(q.symbol, q.provider)
        if st is None or (st.quote_ts is not None and q.ts < st.quote_ts):
            return
        st.bid, st.bid_size, st.ask, st.ask_size, st.quote_ts = q.bid, q.bid_size, q.ask, q.ask_size, q.ts
        if st.last_recv is None:
            st.last_recv = q.recv
        self.dirty.add(st.symbol)

    def on_bar(self, symbol: str, bar: Bar) -> None:
        st = self.states.get(symbol)
        if st is None or not bar.source.startswith(st.provider):
            return
        out_of_order = bool(st.bars) and bar.start < next(reversed(st.bars))
        st.bars[bar.start] = bar  # provider's official minute bar replaces the provisional trade-built one
        if out_of_order:
            st.bars = OrderedDict(sorted(st.bars.items()))
        self.bar_dirty.setdefault(symbol, set()).add(bar.start)
        self.dirty.add(symbol)

    def on_index(self, symbol: str, value: float, ts: int, feed: str) -> None:
        st = self.states.get(symbol)
        if st is None:
            return
        d, _ = self.bounds.classify(ts)
        self._roll_day(st, d)
        if st.last_ts is None or ts >= st.last_ts:
            st.last, st.last_ts, st.last_recv = value, ts, now_ns()
            st.day_high = value if st.day_high is None else max(st.day_high, value)
            st.day_low = value if st.day_low is None else min(st.day_low, value)
            st.day_open = st.day_open if st.day_open is not None else value
        self.dirty.add(symbol)

    # -- status & views -------------------------------------------------------------------------
    def status(self, st: SymbolState) -> tuple[Status, str]:
        if st.provider and licensing.blocked([st.provider]):
            return Status.UNAVAILABLE, "not licensed for public display here"
        up = self._stream_for(st)
        if up is None or (not st.seeded and st.last is None):
            return Status.UNAVAILABLE, st.seed_error or "no data yet"
        sess = cal.session_at()
        if sess == cal.Session.CLOSED:
            return Status.CLOSED, "market closed; last verified values"
        if not up.healthy:
            return Status.STALE, f"{up.name} stream {up.state}" + (f": {up.error}" if up.error else "")
        s = get_settings()
        limit = s.stale_regular_s if sess == cal.Session.REGULAR else s.stale_extended_s
        age = (now_ns() - (st.last_recv or 0)) / NS
        if st.last_recv is None or age > limit:
            return Status.STALE, f"no update for {int(age)}s during {sess}"
        return (Status.DELAYED, f"{st.delay_s // 60}-minute delayed feed") if st.kind == Kind.DELAYED else (Status.LIVE, "real-time feed")

    def view(self, symbol: str) -> dict:
        st = self.states.get(symbol.upper())
        if st is None:
            return {"symbol": symbol.upper(), "status": "UNAVAILABLE", "reason": "not subscribed"}
        status, reason = self.status(st)
        if status == Status.UNAVAILABLE:
            return {"symbol": st.symbol, "status": str(status), "reason": reason, "provider": st.provider}
        ref = st.prev_close
        chg = (st.last - ref) if (st.last is not None and ref) else None
        return {
            "symbol": st.symbol, "status": str(status), "reason": reason, "session": str(cal.session_at()),
            "provider": st.provider, "feed": st.feed, "authority": alpaca.AUTHORITY.get(st.feed, "") if st.provider == "alpaca" else
            ("Consolidated tape via Massive" if not st.is_index else "Cboe index value via Massive"),
            "kind": str(st.kind), "delay_s": st.delay_s,
            "last": st.last, "last_ts": iso(st.last_ts), "recv_ts": iso(st.last_recv),
            "bid": st.bid, "ask": st.ask, "bid_size": st.bid_size, "ask_size": st.ask_size, "quote_ts": iso(st.quote_ts),
            "open": st.day_open, "high": st.day_high, "low": st.day_low, "volume": st.day_volume or None, "vwap": st.vwap(),
            "prev_close": ref, "change": chg, "change_pct": (chg / ref) if chg is not None and ref else None,
            "ext_last": st.ext_last, "ext_ts": iso(st.ext_ts), "session_date": st.session_date.isoformat() if st.session_date else None,
            "stats": {"events": st.events, "duplicates": st.duplicates, "out_of_order": st.out_of_order, "late": st.late},
            "method": "Day high/low/open/VWAP from regular-session trades excluding odd-lot, average-price and out-of-sequence prints; "
                      "seeded from the provider snapshot. " + ("IEX feed covers one venue (~2-3% of volume): volume and VWAP are IEX-only."
                                                               if st.feed == "iex" else ""),
        }

    def bars(self, symbol: str) -> list[dict]:
        st = self.states.get(symbol.upper())
        if st is None or self.status(st)[0] == Status.UNAVAILABLE:
            return []
        now = now_ns()
        out = []
        for b in st.bars.values():
            d = b.to_json()
            d["final"] = b.final or now - st.delay_s * NS > b.start + MIN + 2 * NS
            out.append(d)
        return out

    def take_dirty(self) -> tuple[set[str], dict[str, set[int]]]:
        d, b = self.dirty, self.bar_dirty
        self.dirty, self.bar_dirty = set(), {}
        return d, b

    def providers_status(self) -> list[dict]:
        out = []
        for s in [*self.streams, self.index_stream]:
            if s is None:
                continue
            out.append({"provider": s.name, "feed": s.feed, "kind": str(s.kind), "delay_s": s.delay_s, "state": s.state, "error": s.error,
                        "active": s is self.active or s is self.index_stream, "symbols": len(s.subscribed), "max_symbols": s.max_symbols,
                        "messages": s.messages, "reconnects": s.reconnects,
                        "last_message": datetime.fromtimestamp(s.last_msg, timezone.utc).isoformat() if s.last_msg else None})
        return out

    # -- maintenance: grace unsubscribes, failover ----------------------------------------------
    async def _maintain(self) -> None:
        st = get_settings()
        while True:
            await asyncio.sleep(5)
            now = time.time()
            gone = [s for s, t in self.released.items() if now - t > st.upstream_unsubscribe_grace_s and self.refs.get(s, 0) == 0]
            for s in gone:
                self.released.pop(s, None)
                self.states.pop(s, None)
            if gone:
                for up, idx in ((self.active, False), (self.index_stream, True)):
                    if up:
                        await up.set_symbols({s for s, x in self.states.items() if x.is_index == idx})
            await self._failover()

    async def _failover(self) -> None:
        if not self.active or len(self.streams) < 2:
            return
        if self.active.healthy or self.active.state == "connecting":
            self._down_since = None
            return
        self._down_since = self._down_since or time.time()
        if time.time() - self._down_since < 30:
            return
        nxt = next((s for s in self.streams if s is not self.active and not licensing.blocked([s.name])), None)
        if nxt is None:
            return
        log.warning("failover: %s -> %s", self.active.name, nxt.name)
        old, self.active, self._down_since = self.active, nxt, None
        await old.stop()
        old._task = None
        nxt.start()
        for st in [x for x in self.states.values() if not x.is_index]:
            fresh = SymbolState(st.symbol)
            self.states[st.symbol] = fresh
            await self._attach(fresh)


hub = Hub()
