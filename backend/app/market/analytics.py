"""Market-wide analytics built from live provider data: S&P 500 breadth (history + intraday provisional row)
and options-derived risk measures (Massive options chain snapshot)."""

import logging
import threading
import time
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from ..config import get_settings
from ..quant.breadth import breadth_frame
from . import calendar as cal
from . import history, licensing, reference
from .cache import TTLCache
from .http import ProviderError
from .model import Unavailable
from .providers import alpaca, massive

log = logging.getLogger(__name__)


class Breadth:
    """Daily breadth over S&P 500 members (point-in-time membership when FMP change history is available),
    plus a provisional intraday row from constituent snapshots during the session."""

    def __init__(self):
        self.closes: pd.DataFrame | None = None
        self.mask: pd.DataFrame | None = None
        self.frame: pd.DataFrame | None = None
        self.info: dict = {"available": False, "note": "not loaded yet"}
        self.provisional: dict | None = None
        self.version = 0
        self._lock = threading.Lock()
        self._built_for: date | None = None

    def build(self) -> None:
        st = get_settings()
        if not st.breadth_enabled:
            self.info = {"available": False, "note": "Breadth disabled (BREADTH_ENABLED=false)."}
            return
        last_done = cal.last_completed_session()
        if self._built_for == last_done and self.frame is not None:
            return
        with self._lock:
            try:
                members, intervals, ref_provider = reference.sp500()
                universe = sorted(set(members) | ({r["symbol"] for r in intervals} if intervals else set()))
                bars, provider = history.daily_many(universe)
            except (Unavailable, ProviderError) as e:
                self.info = {"available": False, "note": str(e)}
                return
            closes = pd.DataFrame({s: df["close"] for s, df in bars.items() if len(df)}).sort_index()
            closes = closes[closes.index.date <= last_done]
            mask = reference.membership_mask(intervals, closes.index, list(closes.columns)) if intervals else None
            self.closes, self.mask = closes, mask
            self.frame = breadth_frame(closes, mask)
            self._built_for = last_done
            self.version += 1
            current_cov = closes.iloc[-1][[m for m in members if m in closes.columns]].notna().sum() if len(closes) else 0
            self.info = {
                "available": True, "providers": sorted({provider, ref_provider}), "price_provider": provider, "members_today": len(members),
                "members_with_data_today": int(current_cov), "first": closes.index[0].date().isoformat(), "last": closes.index[-1].date().isoformat(),
                "survivorship_bias": intervals is None,
                "note": ("Point-in-time S&P 500 membership from FMP's change history; delisted members count only while their prices exist at "
                         f"{provider}." if intervals is not None else "Current members only: history is survivorship-biased (FMP change history unavailable)."),
            }
            log.info("breadth built: %d symbols via %s, %s..%s", closes.shape[1], provider, self.info["first"], self.info["last"])

    def refresh_intraday(self) -> None:
        """Provisional breadth for the current session from constituent snapshots (REST; 500 symbols exceed stream limits)."""
        if self.closes is None or cal.session_at() not in (cal.Session.REGULAR, cal.Session.POST):
            self.provisional = None
            return
        provider = self.info.get("price_provider")
        try:
            members, _, _ = reference.sp500()
            snaps = (alpaca.snapshots if provider == "alpaca" else massive.snapshots)(members)
        except (ProviderError, Unavailable) as e:
            log.warning("intraday breadth snapshot failed: %s", e)
            self.provisional = None
            return
        today = pd.Timestamp(datetime.now(timezone.utc).astimezone(cal.ET).date())
        row = pd.Series({s: v.get("last") or v.get("day_close") for s, v in snaps.items()}, dtype=float)
        closes = pd.concat([self.closes, row.reindex(self.closes.columns).to_frame(today).T])
        mask = None
        if self.mask is not None:  # today's members are the current constituent list
            today_mask = pd.DataFrame([self.mask.columns.isin(members)], index=[today], columns=self.mask.columns)
            mask = pd.concat([self.mask, today_mask])
        tail = breadth_frame(closes, mask).iloc[-1]  # full history: summation index and EMAs need it
        self.provisional = {"as_of": datetime.now(timezone.utc).isoformat(), "row": tail.to_dict(), "names": int(row.notna().sum()),
                            "provider": provider}
        self.version += 1

    def sources(self) -> set[str]:
        return set(self.info.get("providers", []))


breadth = Breadth()
_opt_cache = TTLCache(maxsize=16)


def options_summary(underlying: str, spot: float | None = None) -> dict:
    """Put/call ratios, 30-day ATM implied volatility and a gamma-exposure estimate from the option chain snapshot."""
    st = get_settings()
    if not (st.massive_api_key and st.massive_options):
        raise Unavailable("Options analytics need a Massive Options plan (MASSIVE_OPTIONS=true).")
    licensing.check(["massive"], "Options analytics")

    def load():
        chain = massive.option_chain(underlying)
        return compute_options(chain, spot), 600

    return _opt_cache.get_or_compute(f"opt:{underlying}", load)


def compute_options(chain: list[dict], spot: float | None = None) -> dict:
    today = datetime.now(timezone.utc).date()
    rows = []
    for c in chain:
        d, g = c.get("details") or {}, c.get("greeks") or {}
        ua = c.get("underlying_asset") or {}
        spot = spot or ua.get("price")
        try:
            exp = date.fromisoformat(d["expiration_date"])
        except (KeyError, ValueError):
            continue
        rows.append({"type": d.get("contract_type"), "strike": d.get("strike_price"), "dte": (exp - today).days, "iv": c.get("implied_volatility"),
                     "gamma": g.get("gamma"), "oi": c.get("open_interest") or 0, "volume": (c.get("day") or {}).get("volume") or 0})
    df = pd.DataFrame(rows)
    if df.empty or not spot:
        raise Unavailable("Option chain snapshot returned no usable contracts.")
    calls, puts = df[df["type"] == "call"], df[df["type"] == "put"]
    near = df[(df["dte"] >= 20) & (df["dte"] <= 45) & df["iv"].notna()]
    atm_iv = None
    if len(near):
        k = near.iloc[(near["strike"] - spot).abs().argsort()[:4]]
        atm_iv = float(k["iv"].mean())
    g = df.dropna(subset=["gamma"])
    sign = np.where(g["type"] == "call", 1.0, -1.0)
    gex = float((g["gamma"] * g["oi"] * 100 * spot**2 * 0.01 * sign).sum())
    return {
        "underlying": chain[0].get("underlying_asset", {}).get("ticker") if chain else None,
        "spot": spot, "contracts": int(len(df)),
        "put_call_volume": float(puts["volume"].sum() / calls["volume"].sum()) if calls["volume"].sum() > 0 else None,
        "put_call_oi": float(puts["oi"].sum() / calls["oi"].sum()) if calls["oi"].sum() > 0 else None,
        "atm_iv_30d": atm_iv,
        "gamma_exposure_usd_per_1pct": gex,
        "method": "GEX = sum(gamma x OI x 100 x spot^2 x 1%), calls +, puts - (assumes dealers long calls / short puts; a convention, not observed positioning). "
                  "ATM IV: mean IV of the 4 strikes nearest spot, 20-45 days to expiry.",
        "provider": "massive", "as_of": datetime.now(timezone.utc).isoformat(), "kind": "options snapshot (no history: excluded from scores and backtests)",
    }
