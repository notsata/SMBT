"""Public, anonymous, stateless API. No accounts, no database, no stored user data: every request is answered from
provider data held in process memory, and per-visitor state (live symbols, session alerts) lives only as long as
that visitor's WebSocket connection."""

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import alerts as A
from . import analyst, scanner, ws
from . import services as S
from .backtest import validation as V
from .backtest.strategies import STRATEGIES
from .config import ROOT, get_settings
from .market import calendar as cal
from .market import history, reference
from .market.analytics import breadth, options_summary
from .market.cache import TTLCache
from .market.hub import hub
from .market.macro import macro
from .market.model import LicenseBlocked, Unavailable
from .quant import analogs

log = logging.getLogger("smbt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_heavy = TTLCache(maxsize=128)


async def _every(seconds: float, fn, *, thread: bool = True, only_sessions: tuple = ()):
    while True:
        try:
            if not only_sessions or cal.session_at() in only_sessions:
                await (asyncio.to_thread(fn) if thread else fn())
        except Exception as e:  # keep the loop alive; the failure shows in /api/status
            log.warning("background %s failed: %s", getattr(fn, "__name__", fn), e)
        await asyncio.sleep(seconds)


def _warm() -> None:
    st = get_settings()
    for sym in dict.fromkeys(st.benchmark_list + ["^VIX", "^VIX3M", "TLT"]):
        try:
            history.daily(sym)
        except Unavailable as e:
            log.warning("warm-up %s: %s", sym, e)


def _refresh_scores() -> None:
    for sym in get_settings().benchmark_list:
        try:
            snap = S.refresh_live_scores(sym)
        except Unavailable:
            continue
        if snap:
            ws.push_scores(sym, snap)


@asynccontextmanager
async def lifespan(_):
    st = get_settings()
    log.info("starting in %s mode; streams=%s history=%s", st.access_mode, st.stream_order, st.history_order)
    pinned = list(st.benchmark_list) + (["^VIX"] if st.massive_api_key and st.massive_indices else [])
    await hub.start(pinned)
    tasks = [
        asyncio.create_task(ws.fanout_loop()),
        asyncio.create_task(ws.alerts_loop()),
        asyncio.create_task(_every(15 * 60, macro.refresh_all)),
        asyncio.create_task(_every(6 * 3600, _warm)),
        asyncio.create_task(_every(10 * 60, breadth.build)),
        asyncio.create_task(_every(st.breadth_refresh_s, breadth.refresh_intraday, only_sessions=(cal.Session.REGULAR, cal.Session.POST))),
        asyncio.create_task(_every(st.score_refresh_s, _refresh_scores, only_sessions=(cal.Session.REGULAR, cal.Session.POST))),
    ]
    yield
    for t in tasks:
        t.cancel()
    await hub.stop()


app = FastAPI(title="SMBT Market Turning Point Research API", version="2.0.0", lifespan=lifespan)


@app.exception_handler(LicenseBlocked)
def _lic(_, e):
    return JSONResponse({"detail": str(e), "status": "UNAVAILABLE", "reason": "license"}, status_code=451)


@app.exception_handler(Unavailable)
def _unavail(_, e):
    return JSONResponse({"detail": str(e), "status": "UNAVAILABLE"}, status_code=503)


@app.exception_handler(ValueError)
def _ve(_, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


# ------------------------------------------------------------------ in-memory per-IP rate limits (public site)

_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def limit(request: Request, bucket: str, n: int, per_s: float) -> None:
    ip = request.client.host if request.client else "?"
    q = _hits[(bucket, ip)]
    now = time.time()
    while q and q[0] < now - per_s:
        q.popleft()
    if len(q) >= n:
        raise HTTPException(429, f"Rate limit: {n} {bucket} requests per {int(per_s)}s. Try again shortly.")
    q.append(now)
    if len(_hits) > 50_000:  # bound memory under abuse
        _hits.clear()


@app.middleware("http")
async def _general_limit(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        try:
            limit(request, "api", get_settings().rate_limit_per_min, 60)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=429)
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # market data must not persist in browser or proxy caches
    return resp


# ------------------------------------------------------------------ market data

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return S.status()


@app.get("/api/symbols")
def symbols():
    return S.symbols()


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=40)):
    return {"results": reference.search(q)}


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    return hub.view(symbol)


@app.get("/api/overview")
def overview(symbol: str | None = None):
    return S.overview(symbol)


@app.get("/api/chart/{symbol}")
def chart(symbol: str):
    return S.chart(symbol)


@app.get("/api/intraday/{symbol}")
def intraday(symbol: str):
    return S.intraday(symbol)


@app.get("/api/indicators/{symbol}")
def indicators(symbol: str, names: str = "rsi14"):
    return S.indicator_series(symbol, [n.strip() for n in names.split(",") if n.strip()][:8])


@app.get("/api/signals/{symbol}")
def signals(symbol: str, bottom: float = Query(70, ge=0, le=100), top: float = Query(70, ge=0, le=100)):
    return S.signals(symbol, bottom, top)


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str):
    return S.analyze(symbol)


@app.get("/api/options/{symbol}")
def options(symbol: str):
    q = hub.view(symbol)
    return S.clean(options_summary(symbol.upper(), q.get("last")))


@app.get("/api/scanner")
def scan(timeframe: str = Query("daily", pattern="^(daily|weekly)$"), direction: str = "bullish", sector: str = "", kind: str = "", cap: str = "",
         signal: str = "", q: str = ""):
    return scanner.scan(timeframe, direction, sector, kind, cap, signal, q)


@app.get("/api/analogs")
def get_analogs(request: Request, symbol: str | None = None, k: int = Query(10, ge=3, le=25)):
    sym = (symbol or S.default_benchmark()).upper()
    f, _, meta = S.market_frame(sym)
    key = f"analogs:{sym}:{k}:{f.index[-1].date()}:{len(f.columns)}"
    return _heavy.get_or_compute(key, lambda: (S.clean({"symbol": sym, "basis": "confirmed closes", "sources": meta["sources"],
                                                         **analogs.find_analogs(f, k=k), "key_dates": analogs.compare_key_dates(f)}), 3600))


# ------------------------------------------------------------------ validation (heavy: per-IP limited)

@app.get("/api/strategies")
def strategies():
    return [{"id": s.id, "name": s.name, "description": s.description, "grid": s.grid, "defaults": s.defaults, "needs": list(s.needs)} for s in STRATEGIES.values()]


class BacktestReq(BaseModel):
    symbol: str | None = Field(None, max_length=16)
    strategy: str = "trend200"
    train_years: int = Field(6, ge=2, le=20)
    test_years: int = Field(2, ge=1, le=5)
    commission_bps: float = Field(1.0, ge=0, le=100)
    slippage_bps: float = Field(2.0, ge=0, le=100)
    cv: bool = True


@app.post("/api/backtest")
def backtest(req: BacktestReq, request: Request):
    if req.strategy not in STRATEGIES:
        raise HTTPException(400, f"unknown strategy; choose from {list(STRATEGIES)}")
    sym = (req.symbol or S.default_benchmark()).upper()
    f, sf, meta = S.market_frame(sym)
    key = f"bt:{sym}:{f.index[-1].date()}:{req.model_dump_json()}"
    if (hit := _heavy.get(key)) is not None:
        return hit
    limit(request, "backtest", get_settings().heavy_rate_limit_per_min, 60)

    def compute():
        b = S.bars(sym)
        rf = f["rf_rate"] if f["rf_rate"].notna().any() else None
        extra = {f"{x} buy & hold": bx for x in ("SPY", "QQQ") if x != sym and (bx := S._maybe(x)) is not None}
        res = V.walk_forward(b, f, sf, req.strategy, req.train_years, req.test_years, req.commission_bps, req.slippage_bps, rf, extra)
        if req.cv and "error" not in res and req.strategy != "buy_hold":
            res["purged_cv"] = V.purged_cv(b, f, sf, req.strategy, commission_bps=req.commission_bps, slippage_bps=req.slippage_bps, rf=rf)
        res["symbol"] = sym
        res["data"] = {"provider": b.attrs["provider"], "authority": b.attrs["authority"], "adjustment": b.attrs["adjustment"], "first": b.attrs["first"],
                       "last": b.attrs["last"], "sources": meta["sources"], "basis": "confirmed closes",
                       "survivorship": "Single index/ETF series: no constituent survivorship bias in returns. Breadth inputs: " + meta["breadth"].get("note", "n/a")}
        return S.clean(res), 3600

    return _heavy.get_or_compute(key, compute)


@app.get("/api/signal-stats/{symbol}")
def signal_stats(symbol: str, request: Request, signal: str = "bottom_score", threshold: float = Query(70, ge=0, le=100),
                 cooldown: int = Query(20, ge=1, le=252), pivot_pct: float = Query(0.10, ge=0.03, le=0.5)):
    sym = symbol.upper()
    f, sf, _ = S.market_frame(sym)
    key = f"ss:{sym}:{f.index[-1].date()}:{signal}:{threshold}:{cooldown}:{pivot_pct}"
    if (hit := _heavy.get(key)) is not None:
        return hit
    limit(request, "signal-stats", get_settings().heavy_rate_limit_per_min * 3, 60)
    try:
        res = S.clean(V.signal_report(S.bars(sym), f, sf, signal, threshold, cooldown, pivot_pct))
    except KeyError:
        raise HTTPException(400, f"unknown signal {signal}; available: {list(V.signal_library(f, sf))}")
    _heavy.put(key, res, 3600)
    return res


@app.get("/api/scoreboard/{symbol}")
def scoreboard(symbol: str, horizon: int = Query(60, ge=5, le=252)):
    sym = symbol.upper()
    f, sf, _ = S.market_frame(sym)
    return _heavy.get_or_compute(f"sb:{sym}:{f.index[-1].date()}:{horizon}", lambda: (S.clean({
        "symbol": sym, "horizon": horizon, "rows": V.signal_scoreboard(S.bars(sym), f, sf, horizon), "basis": "confirmed closes",
        "note": "p: one-sided vs 1000 random entry sets. q: Benjamini-Hochberg FDR across all signals shown; prefer q < 0.10."}), 3600))


@app.get("/api/alerts/kinds")
def alert_kinds():
    return {"kinds": A.KINDS, "scores": A.SCORES, "breadth_metrics": A.BREADTH, "scanner_flags": {k: v[0] for k, v in scanner.FLAGS.items()},
            "note": "Alerts run only while this page is open (session-only; nothing is stored). Refreshing clears them."}


# ------------------------------------------------------------------ analyst


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    history: list[dict] = Field(default_factory=list, max_length=20)


@app.post("/api/analyst")
def ask(body: AskIn, request: Request):
    limit(request, "analyst", get_settings().analyst_per_hour, 3600)
    try:
        return analyst.ask(body.question, body.history)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except anthropic.RateLimitError:
        raise HTTPException(429, "AI provider rate limit; retry shortly")
    except anthropic.APIConnectionError:
        raise HTTPException(503, "AI provider unreachable")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"AI provider error ({e.status_code})")


@app.websocket("/ws")
async def websocket(socket: WebSocket):
    await ws.endpoint(socket)


# ------------------------------------------------------------------ static frontend (single-container deploy)

_dist = Path(get_settings().frontend_dist or ROOT / "frontend" / "out")
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
