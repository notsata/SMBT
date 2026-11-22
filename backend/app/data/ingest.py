"""Ingestion orchestration: fetch -> quality-check -> upsert -> log. Safe to re-run (idempotent upserts)."""

import csv
import io
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import Bar, IngestRun, Instrument, Membership, SeriesObs, bump_data_version, kv_set, upsert, utcnow
from . import providers as P
from .catalog import CATALOG, CBOE_INDEXES
from .quality import clean_bars

log = logging.getLogger(__name__)
SAMPLE_SOURCE = "sample: arch package bundle (originally Yahoo Finance / FRED exports)"


def _log_run(s: Session, source: str, target: str, status: str, rows: int, message: str = "") -> dict:
    s.add(IngestRun(source=source, target=target, status=status, rows=rows, message=message[:2000]))
    return {"source": source, "target": target, "status": status, "rows": rows, "message": message}


def _bar_rows(symbol: str, df: pd.DataFrame, source: str) -> list[dict]:
    now = utcnow()
    nan = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
    return [
        {
            "symbol": symbol,
            "date": d,
            "open": nan(r.open),
            "high": nan(r.high),
            "low": nan(r.low),
            "close": float(r.close),
            "adj_close": nan(r.adj_close),
            "volume": nan(r.volume),
            "source": source,
            "fetched_at": now,
        }
        for d, r in df.iterrows()
    ]


def sync_instruments(s: Session, settings: Settings) -> int:
    with open(settings.universe_file, newline="") as f:
        rows = [
            {
                "symbol": r["symbol"].strip().upper(),
                "name": r["name"],
                "sector": r["sector"],
                "kind": r["kind"],
                "cap_bucket": r["cap_bucket"],
                "sector_etf": r["sector_etf"],
                "in_scanner": True,
            }
            for r in csv.DictReader(f)
        ]
    rows += [
        {"symbol": sym, "name": f"Cboe {name} index", "sector": "Volatility", "kind": "index", "cap_bucket": "", "sector_etf": "", "in_scanner": False}
        for sym, name in CBOE_INDEXES.items()
    ]
    return upsert(s, Instrument, rows, ["symbol"])


def ingest_constituents(s: Session, http: P.Http, settings: Settings) -> dict:
    try:
        members = P.sp500_constituents(http, settings.sp500_constituents_url)
    except Exception as e:
        return _log_run(s, "sp500-constituents", "SP500", "error", 0, str(e))
    existing = {i.symbol for i in s.execute(select(Instrument)).scalars()}
    s.execute(update(Instrument).values(in_breadth=False))
    new = [
        {"symbol": m["symbol"], "name": m["name"], "sector": m["sector"], "kind": "stock", "cap_bucket": "large", "sector_etf": "", "in_scanner": False, "in_breadth": True}
        for m in members
        if m["symbol"] not in existing
    ]
    upsert(s, Instrument, new, ["symbol"])
    s.execute(update(Instrument).where(Instrument.symbol.in_([m["symbol"] for m in members])).values(in_breadth=True))
    msg = f"{len(members)} current members (current list only: breadth history has survivorship bias unless SP500_HISTORY_URL is set)"
    if settings.sp500_history_url:
        try:
            iv = P.sp500_history(http, settings.sp500_history_url)
            s.execute(delete(Membership).where(Membership.index_id == "SP500"))
            upsert(s, Membership, [{"index_id": "SP500", **r} for r in iv], ["index_id", "symbol", "start_date"])
            msg = f"{len(members)} current members; {len(iv)} historical membership intervals"
        except Exception as e:
            msg += f"; history fetch failed: {e}"
    return _log_run(s, "sp500-constituents", "SP500", "ok", len(members), msg)


def ingest_symbol(s: Session, http: P.Http, settings: Settings, inst: Instrument) -> dict:
    sym = inst.symbol
    last = s.execute(select(func.max(Bar.date)).where(Bar.symbol == sym)).scalar()
    start = last - timedelta(days=10) if last else None  # overlap re-fetch picks up late corrections
    df, quote, source, errors = pd.DataFrame(), None, "", []
    attempts: list[tuple[str, callable]] = []
    if sym in CBOE_INDEXES:
        attempts.append((P.CBOE, lambda: (P.cboe_index(http, CBOE_INDEXES[sym]), None)))
    if settings.tiingo_api_key and not sym.startswith("^"):
        attempts.append((P.TIINGO, lambda: (P.tiingo_daily(http, settings.tiingo_api_key, sym, start), None)))
    attempts.append((P.YAHOO, lambda: P.yahoo_daily(http, sym, start)))
    for src, fn in attempts:
        try:
            df, quote = fn()
            source = src
            if not df.empty:
                break
        except Exception as e:
            errors.append(f"{src.split(' ')[0]}: {e}")
    if df.empty:
        return _log_run(s, "prices", sym, "error", 0, "; ".join(errors) or "no data")
    if start:
        df = df[df.index >= start]
    df, issues = clean_bars(df, inst.kind)
    n = upsert(s, Bar, _bar_rows(sym, df, source), ["symbol", "date"])
    if quote:
        kv_set(s, f"quote:{sym}", quote)
    return _log_run(s, "prices", sym, "warn" if issues or errors else "ok", n, "; ".join(issues + errors))


def _series_rows(sid: str, df: pd.DataFrame, source: str) -> list[dict]:
    lag = timedelta(days=CATALOG[sid].lag_days if sid in CATALOG else 1)
    now = utcnow()
    out = []
    for d, r in df.iterrows():
        avail = r.get("available_at")
        floor = d + timedelta(days=1)  # never usable on its own observation date
        avail = max(avail, floor) if isinstance(avail, date) and not pd.isna(avail) else d + lag
        out.append({"series_id": sid, "date": d, "value": float(r["value"]), "available_at": avail, "source": source, "fetched_at": now})
    return out


def ingest_series(s: Session, http: P.Http, settings: Settings, sid: str) -> dict:
    sd = CATALOG[sid]
    try:
        if sid in ("PCR_EQUITY", "PCR_TOTAL"):
            ser = P.cboe_putcall(http, "equity" if sid == "PCR_EQUITY" else "total")
            df, src = pd.DataFrame({"value": ser, "available_at": None}), "Cboe archived put/call CSV"
        else:
            df, src = P.fred_series(http, sid, settings.fred_api_key, sd.revised)
    except Exception as e:
        return _log_run(s, "series", sid, "error", 0, str(e))
    n = upsert(s, SeriesObs, _series_rows(sid, df, src), ["series_id", "date"])
    return _log_run(s, "series", sid, "ok", n, src)


AUTO_SERIES = [sid for sid, d in CATALOG.items() if "User import" not in d.source]


def import_series_csv(s: Session, sid: str, text: str, source: str = "user import") -> int:
    """CSV with columns date,value[,available_at] for licensed/self-sourced data (AAII, NAAIM, CAPE, ...)."""
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"date", "value"} <= set(df.columns):
        raise ValueError("CSV needs columns: date,value[,available_at]")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["available_at"] = pd.to_datetime(df["available_at"]).dt.date if "available_at" in df else None
    df = df.dropna(subset=["value"]).set_index("date")
    n = upsert(s, SeriesObs, _series_rows(sid, df, source), ["series_id", "date"])
    _log_run(s, "import", sid, "ok", n, source)
    bump_data_version(s)
    s.commit()
    return n


def run_ingest(s: Session, settings: Settings | None = None, prices: bool = True, series: bool = True, http: P.Http | None = None) -> list[dict]:
    settings = settings or get_settings()
    http = http or P.Http(settings.http_min_interval_s)
    results = []
    sync_instruments(s, settings)
    s.commit()
    if settings.breadth_enabled:
        results.append(ingest_constituents(s, http, settings))
        s.commit()
    if prices:
        insts = s.execute(
            select(Instrument).where((Instrument.in_scanner == True) | (Instrument.in_breadth == True) | (Instrument.kind == "index"))  # noqa: E712
        ).scalars().all()
        insts = [i for i in insts if not (i.kind == "index" and i.symbol not in CBOE_INDEXES)]
        for i, inst in enumerate(insts):
            results.append(ingest_symbol(s, http, settings, inst))
            if i % 25 == 0:
                s.commit()
        s.commit()
    if series:
        for sid in AUTO_SERIES:
            results.append(ingest_series(s, http, settings, sid))
            s.commit()
    bump_data_version(s)
    s.commit()
    return results


def load_sample_data(s: Session, settings: Settings | None = None) -> dict:
    """Seeds real historical data shipped in sample-data/ so the app works without network access."""
    settings = settings or get_settings()
    d = Path(settings.sample_data_dir)
    sync_instruments(s, settings)
    upsert(
        s,
        Instrument,
        [
            {"symbol": "^GSPC", "name": "S&P 500 Index (sample 1999-2018)", "sector": "Broad Market", "kind": "index", "in_scanner": True},
            {"symbol": "^IXIC", "name": "NASDAQ Composite (sample 1999-2018)", "sector": "Broad Market", "kind": "index", "in_scanner": True},
        ],
        ["symbol"],
    )
    n_bars = 0
    for f in sorted(d.glob("bars_*.csv.gz")):
        df = pd.read_csv(f, parse_dates=["date"])
        for sym, g in df.groupby("symbol"):
            g = g.set_index(g["date"].dt.date)[["open", "high", "low", "close", "adj_close", "volume"]].astype(float)
            g, _ = clean_bars(g, "index")
            n_bars += upsert(s, Bar, _bar_rows(sym, g, SAMPLE_SOURCE), ["symbol", "date"])
    n_series = 0
    macro = d / "macro.csv.gz"
    if macro.exists():
        m = pd.read_csv(macro, parse_dates=["date"])
        for sid, g in m.groupby("series_id"):
            g = g.set_index(g["date"].dt.date)[["value"]].assign(available_at=None)
            n_series += upsert(s, SeriesObs, _series_rows(sid, g, SAMPLE_SOURCE), ["series_id", "date"])
    _log_run(s, "sample", "all", "ok", n_bars + n_series, SAMPLE_SOURCE)
    bump_data_version(s)
    s.commit()
    return {"bars": n_bars, "series": n_series}
