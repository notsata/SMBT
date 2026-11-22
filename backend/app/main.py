import hashlib
import hmac
import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import alerts as A
from . import analyst, scanner
from . import services as S
from .backtest import validation as V
from .backtest.strategies import STRATEGIES
from .config import ROOT, get_settings
from .data.catalog import CATALOG
from .data.ingest import import_series_csv, load_sample_data, run_ingest
from .db import AlertEvent, AlertRule, Instrument, PushSub, WatchItem, get_db, init_db, kv_get, kv_set, session
from .quant import analogs

log = logging.getLogger("smbt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

COOKIE = "smbt_session"
SESSION_DAYS = 90


@asynccontextmanager
async def lifespan(_):
    init_db()
    if not get_settings().app_access_token:
        log.warning("APP_ACCESS_TOKEN is not set: API is unauthenticated. Only acceptable on localhost.")
    yield


app = FastAPI(title="SMBT Market Turning Point Research API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(S.NotFound)
def _nf(_, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(ValueError)
def _ve(_, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


# ------------------------------------------------------------------ auth

def _sig(exp: int) -> str:
    key = hashlib.sha256(get_settings().app_access_token.encode()).digest()  # rotating the token revokes sessions
    return hmac.new(key, str(exp).encode(), hashlib.sha256).hexdigest()


def _valid_cookie(v: str | None) -> bool:
    try:
        exp_s, sig = (v or "").split(".", 1)
        return int(exp_s) > time.time() and hmac.compare_digest(sig, _sig(int(exp_s)))
    except ValueError:
        return False


def authed(request: Request) -> bool:
    tok = get_settings().app_access_token
    if not tok:
        return True
    h = request.headers.get("authorization", "")
    if h.startswith("Bearer ") and hmac.compare_digest(h[7:].encode(), tok.encode()):
        return True
    return _valid_cookie(request.cookies.get(COOKIE))


def require_auth(request: Request):
    if not authed(request):
        raise HTTPException(401, "Not authenticated")


_fails: dict[str, deque] = defaultdict(deque)


class Login(BaseModel):
    token: str = Field(max_length=512)


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response):
    st = get_settings()
    ip = request.client.host if request.client else "?"
    q = _fails[ip]
    while q and q[0] < time.time() - 900:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many attempts; try again in 15 minutes")
    if not st.app_access_token:
        return {"authenticated": True, "auth_enabled": False}
    if not hmac.compare_digest(body.token.encode(), st.app_access_token.encode()):
        q.append(time.time())
        raise HTTPException(401, "Invalid access token")
    exp = int(time.time()) + SESSION_DAYS * 86400
    response.set_cookie(COOKIE, f"{exp}.{_sig(exp)}", max_age=SESSION_DAYS * 86400, httponly=True, secure=st.cookie_secure, samesite="lax", path="/")
    return {"authenticated": True, "auth_enabled": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    return {"authenticated": authed(request), "auth_enabled": bool(get_settings().app_access_token)}


@app.get("/api/health")
def health():
    return {"ok": True}


AUTH = [Depends(require_auth)]

# ------------------------------------------------------------------ market data


@app.get("/api/status", dependencies=AUTH)
def status(db: Session = Depends(get_db)):
    return S.status(db)


@app.get("/api/symbols", dependencies=AUTH)
def symbols(db: Session = Depends(get_db)):
    meta = S.symbols_with_data(db)
    insts = {i.symbol: i for i in db.execute(select(Instrument)).scalars()}
    out = []
    for sym, m in sorted(meta.items()):
        i = insts.get(sym)
        if i is None or (i.in_breadth and not i.in_scanner and sym not in get_settings().benchmark_list):
            continue
        out.append({"symbol": sym, "name": i.name, "sector": i.sector, "kind": i.kind, "cap_bucket": i.cap_bucket, **m})
    return {"symbols": out, "default": S.default_benchmark(db) if meta else None}


@app.get("/api/overview", dependencies=AUTH)
def overview(symbol: str | None = None, db: Session = Depends(get_db)):
    return S.overview(db, symbol)


@app.get("/api/chart/{symbol}", dependencies=AUTH)
def chart(symbol: str, db: Session = Depends(get_db)):
    return S.chart(db, symbol)


@app.get("/api/indicators/{symbol}", dependencies=AUTH)
def indicators(symbol: str, names: str = "rsi14", db: Session = Depends(get_db)):
    return S.indicator_series(db, symbol, [n.strip() for n in names.split(",") if n.strip()][:8])


@app.get("/api/signals/{symbol}", dependencies=AUTH)
def signals(symbol: str, bottom: float = Query(70, ge=0, le=100), top: float = Query(70, ge=0, le=100), db: Session = Depends(get_db)):
    return S.signals(db, symbol, bottom, top)


@app.get("/api/analyze/{symbol}", dependencies=AUTH)
def analyze(symbol: str, db: Session = Depends(get_db)):
    return S.analyze(db, symbol)


@app.get("/api/scanner", dependencies=AUTH)
def scan(timeframe: str = Query("daily", pattern="^(daily|weekly)$"), direction: str = "bullish", sector: str = "", kind: str = "", cap: str = "",
         signal: str = "", q: str = "", db: Session = Depends(get_db)):
    return scanner.scan(db, timeframe, direction, sector, kind, cap, signal, q)


@app.get("/api/analogs", dependencies=AUTH)
def get_analogs(symbol: str | None = None, k: int = Query(10, ge=3, le=25), db: Session = Depends(get_db)):
    sym = (symbol or S.default_benchmark(db)).upper()

    def compute():
        f, _, _ = S.market_frame(db, sym)
        return S.clean({"symbol": sym, **analogs.find_analogs(f, k=k), "key_dates": analogs.compare_key_dates(f)})

    return S.cached(db, f"analogs:{sym}:{k}", compute)


# ------------------------------------------------------------------ validation


@app.get("/api/strategies", dependencies=AUTH)
def strategies():
    return [{"id": s.id, "name": s.name, "description": s.description, "grid": s.grid, "defaults": s.defaults, "needs": list(s.needs)} for s in STRATEGIES.values()]


class BacktestReq(BaseModel):
    symbol: str | None = None
    strategy: str = "trend200"
    train_years: int = Field(6, ge=2, le=20)
    test_years: int = Field(2, ge=1, le=5)
    commission_bps: float = Field(1.0, ge=0, le=100)
    slippage_bps: float = Field(2.0, ge=0, le=100)
    cv: bool = True


@app.post("/api/backtest", dependencies=AUTH)
def backtest(req: BacktestReq, db: Session = Depends(get_db)):
    if req.strategy not in STRATEGIES:
        raise HTTPException(400, f"unknown strategy; choose from {list(STRATEGIES)}")
    sym = (req.symbol or S.default_benchmark(db)).upper()

    def compute():
        f, sf, meta = S.market_frame(db, sym)
        b = S.bars(db, sym)
        rf = f["rf_rate"] if f["rf_rate"].notna().any() else None
        extra = {}
        for bsym in ("SPY", "QQQ"):
            if bsym != sym:
                try:
                    extra[f"{bsym} buy & hold"] = S.bars(db, bsym)
                except S.NotFound:
                    pass
        res = V.walk_forward(b, f, sf, req.strategy, req.train_years, req.test_years, req.commission_bps, req.slippage_bps, rf, extra)
        if req.cv and "error" not in res and req.strategy != "buy_hold":
            res["purged_cv"] = V.purged_cv(b, f, sf, req.strategy, commission_bps=req.commission_bps, slippage_bps=req.slippage_bps, rf=rf)
        res["symbol"] = sym
        res["data"] = {"source": meta["price"]["source"], "first": b.index[0].date().isoformat(), "last": b.index[-1].date().isoformat(),
                       "survivorship": "Single index/ETF series: no constituent survivorship bias in returns. Breadth inputs: " + meta["breadth"].get("note", "n/a")}
        return S.clean(res)

    return S.cached(db, f"bt:{sym}:{req.model_dump_json()}", compute)


@app.get("/api/signal-stats/{symbol}", dependencies=AUTH)
def signal_stats(symbol: str, signal: str = "bottom_score", threshold: float = Query(70, ge=0, le=100), cooldown: int = Query(20, ge=1, le=252),
                 pivot_pct: float = Query(0.10, ge=0.03, le=0.5), db: Session = Depends(get_db)):
    sym = symbol.upper()

    def compute():
        f, sf, _ = S.market_frame(db, sym)
        try:
            return S.clean(V.signal_report(S.bars(db, sym), f, sf, signal, threshold, cooldown, pivot_pct))
        except KeyError:
            raise HTTPException(400, f"unknown signal {signal}; available: {list(V.signal_library(f, sf))}")

    return S.cached(db, f"ss:{sym}:{signal}:{threshold}:{cooldown}:{pivot_pct}", compute)


@app.get("/api/scoreboard/{symbol}", dependencies=AUTH)
def scoreboard(symbol: str, horizon: int = Query(60, ge=5, le=252), db: Session = Depends(get_db)):
    sym = symbol.upper()

    def compute():
        f, sf, _ = S.market_frame(db, sym)
        return S.clean({"symbol": sym, "horizon": horizon, "rows": V.signal_scoreboard(S.bars(db, sym), f, sf, horizon),
                        "note": "p: one-sided vs 1000 random entry sets. q: Benjamini-Hochberg FDR across all signals shown; prefer q < 0.10."})

    return S.cached(db, f"sb:{sym}:{horizon}", compute)


# ------------------------------------------------------------------ watchlist, settings


@app.get("/api/watchlist", dependencies=AUTH)
def watchlist(db: Session = Depends(get_db)):
    return [w.symbol for w in db.execute(select(WatchItem).order_by(WatchItem.added_at)).scalars()]


@app.post("/api/watchlist/{symbol}", dependencies=AUTH)
def watch_add(symbol: str, db: Session = Depends(get_db)):
    sym = symbol.upper().strip()
    if not sym or len(sym) > 24:
        raise HTTPException(400, "invalid symbol")
    if not db.get(WatchItem, sym):
        db.add(WatchItem(symbol=sym))
        db.commit()
    return watchlist(db)


@app.delete("/api/watchlist/{symbol}", dependencies=AUTH)
def watch_del(symbol: str, db: Session = Depends(get_db)):
    db.execute(delete(WatchItem).where(WatchItem.symbol == symbol.upper()))
    db.commit()
    return watchlist(db)


@app.get("/api/settings", dependencies=AUTH)
def get_user_settings(db: Session = Depends(get_db)):
    return kv_get(db, "user_settings", {}) or {}


@app.put("/api/settings", dependencies=AUTH)
def put_user_settings(body: dict = Body(...), db: Session = Depends(get_db)):
    if len(str(body)) > 20_000:
        raise HTTPException(413, "settings too large")
    kv_set(db, "user_settings", body)
    db.commit()
    return body


# ------------------------------------------------------------------ alerts + push


class RuleIn(BaseModel):
    name: str = Field(max_length=128)
    kind: str
    params: dict = {}
    cooldown_minutes: int = Field(1440, ge=5, le=60 * 24 * 30)
    enabled: bool = True


def _rule_out(r: AlertRule) -> dict:
    return {"id": r.id, "name": r.name, "kind": r.kind, "params": r.params, "enabled": r.enabled, "cooldown_minutes": r.cooldown_minutes,
            "armed": r.armed, "last_state": r.last_state, "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None}


@app.get("/api/alerts/kinds", dependencies=AUTH)
def alert_kinds():
    return {"kinds": A.KINDS, "scores": A.SCORES, "breadth_metrics": A.BREADTH, "scanner_flags": {k: v[0] for k, v in scanner.FLAGS.items()}}


@app.get("/api/alerts", dependencies=AUTH)
def list_rules(db: Session = Depends(get_db)):
    return [_rule_out(r) for r in db.execute(select(AlertRule).order_by(AlertRule.id)).scalars()]


@app.post("/api/alerts", dependencies=AUTH)
def create_rule(body: RuleIn, db: Session = Depends(get_db)):
    r = AlertRule(name=body.name, kind=body.kind, params=A.validate(body.kind, body.params), cooldown_minutes=body.cooldown_minutes, enabled=body.enabled, armed=True, last_state={})
    db.add(r)
    db.commit()
    return _rule_out(r)


@app.put("/api/alerts/{rid}", dependencies=AUTH)
def update_rule(rid: int, body: RuleIn, db: Session = Depends(get_db)):
    r = db.get(AlertRule, rid)
    if not r:
        raise HTTPException(404, "rule not found")
    r.name, r.kind, r.params, r.cooldown_minutes, r.enabled = body.name, body.kind, A.validate(body.kind, body.params), body.cooldown_minutes, body.enabled
    r.armed, r.last_state = True, {}
    db.commit()
    return _rule_out(r)


@app.delete("/api/alerts/{rid}", dependencies=AUTH)
def delete_rule(rid: int, db: Session = Depends(get_db)):
    db.execute(delete(AlertRule).where(AlertRule.id == rid))
    db.commit()
    return {"ok": True}


@app.get("/api/alerts/events", dependencies=AUTH)
def alert_events(limit: int = Query(50, le=500), db: Session = Depends(get_db)):
    evs = db.execute(select(AlertEvent).order_by(AlertEvent.id.desc()).limit(limit)).scalars()
    return [{"id": e.id, "rule_id": e.rule_id, "fired_at": e.fired_at.isoformat(), "title": e.title, "body": e.body, "payload": e.payload, "delivered": e.delivered} for e in evs]


@app.post("/api/alerts/evaluate", dependencies=AUTH)
def evaluate(db: Session = Depends(get_db)):
    return [{"title": e.title, "body": e.body} for e in A.evaluate_all(db)]


@app.get("/api/push/key", dependencies=AUTH)
def push_key():
    return {"public_key": get_settings().vapid_public_key or None}


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1024, pattern=r"^https://")
    keys: dict


@app.post("/api/push/subscribe", dependencies=AUTH)
def push_sub(body: PushSubIn, request: Request, db: Session = Depends(get_db)):
    if not {"p256dh", "auth"} <= body.keys.keys():
        raise HTTPException(400, "missing keys")
    db.merge(PushSub(endpoint=body.endpoint, p256dh=body.keys["p256dh"][:256], auth=body.keys["auth"][:128], user_agent=request.headers.get("user-agent", "")[:256]))
    db.commit()
    return {"ok": True}


@app.post("/api/push/unsubscribe", dependencies=AUTH)
def push_unsub(body: dict = Body(...), db: Session = Depends(get_db)):
    db.execute(delete(PushSub).where(PushSub.endpoint == str(body.get("endpoint", ""))))
    db.commit()
    return {"ok": True}


@app.post("/api/push/test", dependencies=AUTH)
def push_test(db: Session = Depends(get_db)):
    n = A.send_push(db, {"title": "SMBT test notification", "body": "Push notifications are working.", "url": "/alerts"})
    return {"delivered": n}


# ------------------------------------------------------------------ analyst


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    history: list[dict] = []


@app.post("/api/analyst", dependencies=AUTH)
def ask(body: AskIn, db: Session = Depends(get_db)):
    try:
        return analyst.ask(db, body.question, body.history)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except anthropic.RateLimitError:
        raise HTTPException(429, "AI provider rate limit; retry shortly")
    except anthropic.APIConnectionError:
        raise HTTPException(503, "AI provider unreachable")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"AI provider error ({e.status_code})")


# ------------------------------------------------------------------ admin

_ingest_lock = threading.Lock()


def _bg_ingest():
    if not _ingest_lock.acquire(blocking=False):
        return
    s = session()
    try:
        kv_set(s, "ingest_status", {"state": "running", "started": time.time()})
        s.commit()
        res = run_ingest(s)
        errs = sum(r["status"] == "error" for r in res)
        kv_set(s, "ingest_status", {"state": "done", "finished": time.time(), "jobs": len(res), "errors": errs})
        s.commit()
        A.evaluate_all(s)
    except Exception as e:
        log.exception("ingest failed")
        s.rollback()
        kv_set(s, "ingest_status", {"state": "failed", "error": str(e)[:500]})
        s.commit()
    finally:
        s.close()
        _ingest_lock.release()


@app.post("/api/admin/ingest", dependencies=AUTH)
def admin_ingest():
    if _ingest_lock.locked():
        return {"started": False, "reason": "already running"}
    threading.Thread(target=_bg_ingest, daemon=True).start()
    return {"started": True}


@app.get("/api/admin/ingest", dependencies=AUTH)
def admin_ingest_status(db: Session = Depends(get_db)):
    return kv_get(db, "ingest_status", {"state": "never"})


@app.post("/api/admin/sample", dependencies=AUTH)
def admin_sample(db: Session = Depends(get_db)):
    return load_sample_data(db)


@app.post("/api/admin/import/{series_id}", dependencies=AUTH)
async def admin_import(series_id: str, request: Request):
    if series_id not in CATALOG:
        raise HTTPException(400, f"unknown series; choose from {list(CATALOG)}")
    raw = await request.body()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "file too large")
    s = session()
    try:
        return {"rows": import_series_csv(s, series_id, raw.decode("utf-8", "replace"), "user import")}
    finally:
        s.close()


# ------------------------------------------------------------------ static frontend (single-container deploy)

_dist = Path(get_settings().frontend_dist or ROOT / "frontend" / "out")
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
