import logging
from collections.abc import Iterator
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Bar(Base):
    """Completed daily sessions only; partial intraday bars are never stored."""

    __tablename__ = "bars"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SeriesObs(Base):
    """Macro / sentiment observations. available_at = first date the value was publicly known (point-in-time)."""

    __tablename__ = "series_obs"
    series_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    available_at: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    sector: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(16), default="stock")  # etf | stock | index
    cap_bucket: Mapped[str] = mapped_column(String(16), default="")  # mega | large | mid | small | ""
    sector_etf: Mapped[str] = mapped_column(String(16), default="")
    in_breadth: Mapped[bool] = mapped_column(Boolean, default=False)
    in_scanner: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Base):
    """Historical index membership intervals for survivorship-bias control. end_date NULL = still a member."""

    __tablename__ = "membership"
    index_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    end_date: Mapped[date | None] = mapped_column(Date)


class WatchItem(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    armed: Mapped[bool] = mapped_column(Boolean, default=True)
    last_state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    delivered: Mapped[int] = mapped_column(Integer, default=0)


class PushSub(Base):
    __tablename__ = "push_subscriptions"
    endpoint: Mapped[str] = mapped_column(String(1024), primary_key=True)
    p256dh: Mapped[str] = mapped_column(String(256))
    auth: Mapped[str] = mapped_column(String(128))
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16))  # ok | warn | error
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")


class KV(Base):
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


_engine = None
_Session: sessionmaker | None = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Tests switch DATABASE_URL at runtime."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


def session() -> Session:
    engine()
    return _Session()


def get_db() -> Iterator[Session]:
    s = session()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    # shortcut: create_all instead of Alembic migrations; add Alembic before the first schema change in production.
    eng = engine()
    Base.metadata.create_all(eng)
    if eng.dialect.name == "postgresql":
        with eng.begin() as c:
            try:
                c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                c.execute(text("SELECT create_hypertable('bars', 'date', if_not_exists => TRUE, migrate_data => TRUE)"))
                c.execute(text("SELECT create_hypertable('series_obs', 'date', if_not_exists => TRUE, migrate_data => TRUE)"))
            except Exception as e:  # plain Postgres works; Timescale only adds compression/chunking
                log.warning("TimescaleDB unavailable, using plain PostgreSQL tables: %s", e)


def upsert(s: Session, model, rows: list[dict], keys: list[str]) -> int:
    if not rows:
        return 0
    ins = pg_insert if s.bind.dialect.name == "postgresql" else sqlite_insert
    cols = [c for c in rows[0] if c not in keys]
    for i in range(0, len(rows), 1000):
        stmt = ins(model).values(rows[i : i + 1000])
        stmt = stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in cols})
        s.execute(stmt)
    return len(rows)  # driver rowcount is unreliable for multi-row upserts (psycopg reports -1)


def kv_get(s: Session, key: str, default=None):
    row = s.get(KV, key)
    return row.value if row else default


def kv_set(s: Session, key: str, value) -> None:
    upsert(s, KV, [{"key": key, "value": value, "updated_at": utcnow()}], ["key"])


def bump_data_version(s: Session) -> int:
    v = int((kv_get(s, "data_version") or {}).get("v", 0)) + 1
    kv_set(s, "data_version", {"v": v, "at": utcnow().isoformat()})
    return v
