"""Macro series from FRED, held in memory, refreshed when the source publishes (FRED `last_updated`),
and aligned point-in-time: a value is used only after it was published and only while it is current
(`max_age_days`), so a discontinued or failed series can never leak an old value into today's scores."""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..config import get_settings
from . import licensing
from .http import ProviderError
from .providers import fred

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesDef:
    id: str
    name: str
    category: str
    freq: str  # daily | weekly | monthly | quarterly
    lag_days: int  # conservative publication lag used only without ALFRED vintages
    revised: bool  # use first-release vintages when FRED_API_KEY is set
    note: str = ""

    @property
    def max_age_days(self) -> int:
        """How long after its observation date a value still counts as current."""
        return {"daily": 10, "weekly": self.lag_days + 14, "monthly": self.lag_days + 45, "quarterly": self.lag_days + 120}[self.freq]


CATALOG: dict[str, SeriesDef] = {s.id: s for s in [
    SeriesDef("T10Y2Y", "10Y minus 2Y Treasury spread", "rates", "daily", 1, False),
    SeriesDef("T10Y3M", "10Y minus 3M Treasury spread", "rates", "daily", 1, False),
    SeriesDef("DGS10", "10Y Treasury yield", "rates", "daily", 1, False),
    SeriesDef("DGS2", "2Y Treasury yield", "rates", "daily", 1, False),
    SeriesDef("DFF", "Effective federal funds rate", "rates", "daily", 1, False),
    SeriesDef("DFII10", "10Y TIPS real yield", "rates", "daily", 1, False, "History starts 2003."),
    SeriesDef("DTB3", "3M Treasury bill (risk-free rate)", "rates", "daily", 1, False),
    SeriesDef("BAA10Y", "Moody's Baa corporate minus 10Y Treasury", "credit", "daily", 1, False, "Moody's data: copyright status read from FRED notes."),
    SeriesDef("BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "credit", "daily", 1, False, "ICE data: copyright status read from FRED notes."),
    SeriesDef("NFCI", "Chicago Fed National Financial Conditions Index", "conditions", "weekly", 7, True),
    SeriesDef("UNRATE", "Unemployment rate", "labor", "monthly", 37, True),
    SeriesDef("ICSA", "Initial jobless claims", "labor", "weekly", 6, True),
    SeriesDef("CPIAUCSL", "CPI, all items", "inflation", "monthly", 46, True),
    SeriesDef("CPILFESL", "Core CPI", "inflation", "monthly", 46, True),
    SeriesDef("GACDFSA066MSFRBPHI", "Philadelphia Fed manufacturing: current activity", "activity", "monthly", 21, True,
              "Free substitute for ISM Manufacturing PMI (licensed, not on FRED)."),
    SeriesDef("WALCL", "Fed total assets", "liquidity", "weekly", 2, False),
    SeriesDef("WTREGEN", "Treasury General Account", "liquidity", "weekly", 2, False),
    SeriesDef("RRPONTSYD", "Overnight reverse repo", "liquidity", "daily", 1, False),
    SeriesDef("M2SL", "M2 money stock", "liquidity", "monthly", 56, True),
    SeriesDef("NCBEILQ027S", "Nonfinancial corporate equities, market value", "valuation", "quarterly", 165, True),
    SeriesDef("GDP", "Nominal GDP", "valuation", "quarterly", 122, True),
]}


@dataclass
class SeriesData:
    obs: pd.DataFrame  # index obs date; columns value, available_at (Timestamp)
    source: str
    fetched_at: float
    last_updated: str | None
    copyrighted: bool | None  # None = unknown (no FRED key)
    error: str | None = None

    @property
    def license_tag(self) -> str:
        return "fred" if self.copyrighted is False else "fred-third-party"


class MacroStore:
    META_CHECK_S = 30 * 60  # cheap metadata poll; observations are refetched only when FRED reports an update
    NO_KEY_REFRESH_S = 6 * 3600

    def __init__(self):
        self._data: dict[str, SeriesData] = {}
        self._meta_checked: dict[str, float] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self.version = 0

    def _fetch(self, sid: str, meta: dict | None) -> SeriesData:
        d = CATALOG[sid]
        df, source = fred.observations(sid, d.revised)
        lag = timedelta(days=d.lag_days)
        avail = [max(a, o + timedelta(days=1)) if isinstance(a, date) else o + lag for o, a in zip(df.index, df["available_at"])]
        obs = pd.DataFrame({"value": df["value"].values, "available_at": pd.to_datetime(avail)}, index=pd.to_datetime(df.index))
        obs = obs.sort_index()
        obs["available_at"] = obs["available_at"].cummax()  # never usable before every earlier observation was
        return SeriesData(obs, source, time.time(), (meta or {}).get("last_updated"), (meta or {}).get("copyrighted"))

    def get(self, sid: str, refresh: bool = False) -> SeriesData | None:
        """Loaded series (fetched once on first use). refresh=True runs the update check; only the background
        refresher passes it, so score computations never wait on the network for data already in memory."""
        if sid not in CATALOG:
            return None
        if not refresh and sid in self._data:
            return self._data[sid]
        with self._lock:
            kl = self._key_locks.setdefault(sid, threading.Lock())
        with kl:
            cur = self._data.get(sid)
            has_key = bool(get_settings().fred_api_key)
            due = cur is None
            meta = None
            try:
                if has_key and (cur is None or time.time() - self._meta_checked.get(sid, 0) > self.META_CHECK_S):
                    meta = fred.metadata(sid)
                    self._meta_checked[sid] = time.time()
                    due = due or meta.get("last_updated") != cur.last_updated
                elif not has_key and cur is not None and time.time() - cur.fetched_at > self.NO_KEY_REFRESH_S:
                    due = True
                if due:
                    new = self._fetch(sid, meta or ({"last_updated": cur.last_updated, "copyrighted": cur.copyrighted} if cur else None))
                    self._data[sid] = new
                    self.version += 1
                    log.info("macro %s refreshed (%s, last obs %s)", sid, new.source, new.obs.index[-1].date() if len(new.obs) else None)
            except ProviderError as e:
                if cur is None:
                    self._data[sid] = cur = SeriesData(pd.DataFrame(columns=["value", "available_at"]), "", time.time(), None, None, str(e))
                else:
                    cur.error = str(e)
            return self._data.get(sid)

    def pit(self, sid: str, index: pd.DatetimeIndex, transform=None) -> tuple[pd.Series, str | None]:
        """(values as known on each date, licence tag or None if unavailable). Values older than max_age -> NaN."""
        sd = self.get(sid)
        empty = pd.Series(np.nan, index=index)
        if sd is None or sd.obs.empty:
            return empty, None
        if licensing.blocked([sd.license_tag]):
            return empty, None
        df = sd.obs.copy()
        if transform is not None:
            df["value"] = transform(df["value"]).values
            df = df.dropna(subset=["value"])
        df = df.reset_index(names="obs_date").sort_values(["available_at", "obs_date"])
        prev = df["obs_date"].cummax().shift()
        df = df[prev.isna() | (df["obs_date"] > prev)]
        left = pd.DataFrame({"t": pd.DatetimeIndex(index).astype("datetime64[ns]")})
        df["available_at"] = df["available_at"].astype("datetime64[ns]")
        df["obs_date"] = df["obs_date"].astype("datetime64[ns]")
        m = pd.merge_asof(left, df, left_on="t", right_on="available_at", direction="backward")
        fresh = (m["t"] - m["obs_date"]) <= pd.Timedelta(days=CATALOG[sid].max_age_days)
        return pd.Series(np.where(fresh, m["value"], np.nan), index=index), sd.license_tag

    def refresh_all(self) -> None:
        for sid in CATALOG:
            self.get(sid, refresh=True)

    def status(self) -> list[dict]:
        out = []
        for sid, d in CATALOG.items():
            sd = self._data.get(sid)
            last = sd.obs.index[-1].date() if sd is not None and len(sd.obs) else None
            age = (datetime.now(timezone.utc).date() - last).days if last else None
            out.append({
                "id": sid, "name": d.name, "category": d.category, "freq": d.freq, "note": d.note,
                "available": last is not None and age is not None and age <= d.max_age_days,
                "last_obs": last.isoformat() if last else None,
                "known_since": sd.obs["available_at"].iloc[-1].date().isoformat() if last else None,
                "current": last is not None and age is not None and age <= d.max_age_days,
                "source": sd.source if sd else None, "fred_last_updated": sd.last_updated if sd else None,
                "copyright": None if sd is None or sd.copyrighted is None else sd.copyrighted,
                "error": sd.error if sd else None, "loaded": sd is not None,
            })
        return out


macro = MacroStore()
