"""Provider adapters, normalization, validation, point-in-time macro, licensing and the session calendar."""

from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from app.config import get_settings
from app.market import calendar as cal
from app.market import history, licensing, reference
from app.market.http import Http, ProviderError, set_http
from app.market.macro import CATALOG, MacroStore, SeriesData
from app.market.model import LicenseBlocked, Unavailable, parse_ts
from app.market.providers import alpaca, eod, fmp, fred, massive

ET = cal.ET


def test_parse_ts_keeps_nanoseconds():
    assert parse_ts("2026-11-20T15:51:44.208123456Z") == int(datetime(2026, 11, 20, 15, 51, 44, tzinfo=timezone.utc).timestamp()) * 10**9 + 208123456
    assert parse_ts("2026-11-20T15:51:44Z") % 10**9 == 0
    assert parse_ts("2026-11-20T10:51:44.5-05:00") == parse_ts("2026-11-20T15:51:44.5Z")


def test_alpaca_daily_and_snapshot_parsing():
    rows = [{"t": "2026-11-19T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "n": 3, "vw": 1.4},
            {"t": "2026-11-20T05:00:00Z", "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 20, "n": 4, "vw": 1.8}]
    df = alpaca.parse_daily(rows)
    assert list(df.index.date) == [date(2026, 11, 19), date(2026, 11, 20)] and df["close"].iloc[-1] == 2
    snap = alpaca.parse_snapshot("SPY", {
        "latestTrade": {"t": "2026-11-20T20:59:59.9Z", "x": "V", "p": 601.2, "s": 100, "c": ["@"], "i": 9, "z": "B"},
        "latestQuote": {"t": "2026-11-20T21:00:00Z", "ax": "V", "ap": 601.3, "as": 2, "bx": "V", "bp": 601.1, "bs": 3},
        "dailyBar": {"t": "2026-11-20T05:00:00Z", "o": 598, "h": 603, "l": 597, "c": 601.2, "v": 5e7, "vw": 600.5},
        "prevDailyBar": {"t": "2026-11-19T05:00:00Z", "o": 590, "h": 599, "l": 589, "c": 597.5, "v": 6e7}})
    assert snap["last"] == 601.2 and snap["bid"] == 601.1 and snap["ask"] == 601.3 and snap["prev_close"] == 597.5 and snap["day_vwap"] == 600.5


def test_massive_parsing():
    df = massive.parse_aggs([{"t": 1763614800000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}])  # 2025-11-20 05:00 UTC = midnight ET
    assert df.index[0] == pd.Timestamp("2025-11-20")
    s = massive.parse_snapshot({"ticker": "SPY", "updated": 1763672400000000000, "day": {"o": 1, "h": 2, "l": 0.5, "c": 1.8, "v": 100, "vw": 1.2},
                                "lastTrade": {"p": 1.81, "s": 5, "t": 1763672399000000000, "x": 4, "i": "1"},
                                "lastQuote": {"p": 1.80, "s": 1, "P": 1.82, "S": 2, "t": 1763672399500000000}, "prevDay": {"c": 1.7}})
    assert s["bid"] == 1.80 and s["ask"] == 1.82 and s["last"] == 1.81 and s["prev_close"] == 1.7
    assert massive.api_symbol("^VIX") == "I:VIX" and massive.api_symbol("SPY") == "SPY"


def test_tiingo_cboe_fred_fmp_parsers():
    t = eod.parse_tiingo([{"date": "2026-11-20T00:00:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100,
                           "adjOpen": 5, "adjHigh": 5.5, "adjLow": 4.5, "adjClose": 5.25, "adjVolume": 200}])
    assert t["close"].iloc[0] == 5.25 and t["volume"].iloc[0] == 200  # adjusted series used throughout
    c = eod.parse_cboe("DATE,OPEN,HIGH,LOW,CLOSE\n11/19/2026,17.1,18,16.9,17.5\n11/20/2026,0,0,0,0\n")
    assert len(c) == 1 and c["close"].iloc[0] == 17.5  # zero rows dropped, not kept as data
    assert eod.parse_cboe("DATE,VVIX\n11/20/2026,90.1\n")["close"].iloc[0] == 90.1
    o = fred.parse_observations({"observations": [{"date": "2026-09-01", "value": "3.1", "realtime_start": "2026-10-03"},
                                                   {"date": "2026-10-01", "value": ".", "realtime_start": "2026-11-07"}]}, first_release=True)
    assert len(o) == 1 and o["available_at"].iloc[0] == date(2026, 10, 3)
    g = fred.parse_fredgraph("observation_date,DGS10\n2026-11-19,4.1\n2026-11-20,.\n")
    assert len(g) == 1
    assert fmp.cap_bucket(3e12) == "mega" and fmp.cap_bucket(5e9) == "mid" and fmp.cap_bucket(None) == ""
    assert fmp.fmp_symbol("BRK.B") == "BRK-B" and eod.tiingo_symbol("BRK.B") == "brk-b"


def test_http_retries_and_never_leaks_keys(monkeypatch):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503)

    monkeypatch.setattr("app.market.http.time.sleep", lambda s: None)
    h = Http(0, attempts=3, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError) as e:
        h.get("fred", "https://api.example.test/x", params={"api_key": "SECRET123"}, headers={"Authorization": "Bearer SECRET456"})
    assert calls["n"] == 3 and "SECRET" not in str(e.value)
    h401 = Http(0, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(ProviderError, match="rejected the API key"):
        h401.get("massive", "https://api.example.test/y")


def test_alpaca_pagination_and_headers(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        token = req.url.params.get("page_token")
        page = {"bars": {"SPY": [{"t": "2026-11-19T05:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": "p2"} if not token else \
               {"bars": {"SPY": [{"t": "2026-11-20T05:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}]}, "next_page_token": None}
        return httpx.Response(200, json=page)

    st = get_settings()
    monkeypatch.setattr(st, "alpaca_api_key_id", "KID")
    monkeypatch.setattr(st, "alpaca_api_secret_key", "KSECRET")
    set_http(Http(0, client=httpx.Client(transport=httpx.MockTransport(handler))))
    try:
        out = alpaca.daily_bars(["SPY"], date(2026, 11, 1))
    finally:
        set_http(None)
    assert list(out["SPY"]["close"]) == [1, 2]
    assert seen[0].headers["APCA-API-KEY-ID"] == "KID" and seen[0].url.params["adjustment"] == "all" and seen[0].url.params["feed"] == "sip"


def test_history_validation_drops_bad_rows():
    df = pd.DataFrame({"open": [10, -1, 10], "high": [11, 11, 9], "low": [9, 9, 9.5], "close": [10.5, 10, 0], "volume": [1, 1, 1]},
                      index=pd.DatetimeIndex(["2026-11-17", "2026-11-18", "2026-11-19"]))
    out, issues = history.validate(df)
    assert len(out) == 2 and pd.isna(out.loc["2026-11-18", "open"]) and issues


def test_history_prefers_provider_covering_start_and_drops_in_progress_day(monkeypatch):
    idx_long = pd.bdate_range("1993-02-01", "2026-11-20")
    idx_short = pd.bdate_range("2016-01-04", "2026-11-20")
    frames = {"a": pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx_short),
              "b": pd.DataFrame({"open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 1.0}, index=idx_long)}
    monkeypatch.setattr(history, "candidates", lambda s: ["a", "b"])
    monkeypatch.setattr(history, "_fetch", lambda p, s, start: frames[p])
    monkeypatch.setattr(history.cal, "last_completed_session", lambda now=None: date(2026, 11, 19))
    df, ttl = history._load("ZZZ")
    assert df.attrs["provider"] == "b"  # the one covering HISTORY_START wins; no stitching of a and b
    assert df.index[-1] == pd.Timestamp("2026-11-19")  # 11-20 is the in-progress session here
    assert df.attrs["complete_through_last_session"] is True


def test_history_unavailable_never_invents(monkeypatch):
    monkeypatch.setattr(history, "candidates", lambda s: ["a"])

    def boom(p, s, start):
        raise ProviderError("a", "HTTP 503")

    monkeypatch.setattr(history, "_fetch", boom)
    with pytest.raises(Unavailable, match="no provider returned history"):
        history._load("NOPE")


def _store_with(sid: str, obs_dates, values, avail, copyrighted=False) -> MacroStore:
    m = MacroStore()
    m._data[sid] = SeriesData(pd.DataFrame({"value": values, "available_at": pd.to_datetime(avail)}, index=pd.to_datetime(obs_dates)),
                              "test", 0, None, copyrighted)
    return m


def test_pit_uses_publication_date_and_expires_old_values():
    m = _store_with("UNRATE", ["2026-01-01", "2026-02-01"], [4.0, 4.2], ["2026-02-06", "2026-03-06"])
    idx = pd.DatetimeIndex(["2026-02-05", "2026-02-06", "2026-03-06", "2026-04-20", "2026-06-01"])
    s, tag = m.pit("UNRATE", idx)
    assert pd.isna(s.iloc[0]) and s.iloc[1] == 4.0 and s.iloc[2] == 4.2
    assert s.iloc[3] == 4.2  # within max_age (lag 37 + 45 days from the Feb observation)
    assert pd.isna(s.iloc[4])  # too old: a discontinued/failed series never leaks into today's score
    assert tag == "fred"


def test_pit_derived_value_waits_for_newest_input():
    m = _store_with("CPIAUCSL", [f"2025-{i:02d}-01" for i in range(1, 13)] + ["2026-01-01"], list(range(100, 113)),
                    [f"2025-{i:02d}-14" for i in range(2, 13)] + ["2026-01-14", "2026-02-13"])
    s, _ = m.pit("CPIAUCSL", pd.DatetimeIndex(["2026-02-12", "2026-02-13"]), transform=lambda v: v.pct_change(12) * 100)
    assert pd.isna(s.iloc[0]) and s.iloc[1] == pytest.approx(12.0)


def test_copyrighted_series_blocked_in_public_mode(monkeypatch):
    m = _store_with("BAA10Y", ["2026-11-18"], [1.9], ["2026-11-19"], copyrighted=True)
    st = get_settings()
    s, tag = m.pit("BAA10Y", pd.DatetimeIndex(["2026-11-20"]))
    assert s.iloc[0] == 1.9 and tag == "fred-third-party"  # personal mode: allowed
    monkeypatch.setattr(st, "access_mode", "public")
    s, tag = m.pit("BAA10Y", pd.DatetimeIndex(["2026-11-20"]))
    assert pd.isna(s.iloc[0]) and tag is None
    monkeypatch.setattr(st, "public_display_licenses", "fred-third-party")
    assert m.pit("BAA10Y", pd.DatetimeIndex(["2026-11-20"]))[0].iloc[0] == 1.9


def test_licensing_gate(monkeypatch):
    st = get_settings()
    licensing.check(["alpaca", "fmp"])  # personal mode: everything allowed
    monkeypatch.setattr(st, "access_mode", "public")
    with pytest.raises(LicenseBlocked, match="Alpaca"):
        licensing.check(["alpaca", "fred"])
    licensing.check(["fred", "anthropic"])  # public-domain FRED and the analyst are always allowed
    monkeypatch.setattr(st, "public_display_licenses", "alpaca")
    licensing.check(["alpaca", "fred"])


def test_membership_intervals_walk_back():
    changes = [{"date": "2024-06-24", "symbol": "NEW", "removedTicker": "OLD"}, {"date": "2020-03-02", "symbol": "OLD", "removedTicker": "GONE"}]
    iv = {(r["symbol"], r["start"], r["end"]) for r in reference.membership_intervals(["NEW", "KEEP"], changes)}
    assert ("NEW", date(2024, 6, 24), None) in iv
    assert ("OLD", date(2020, 3, 2), date(2024, 6, 23)) in iv
    assert ("GONE", date(1900, 1, 1), date(2020, 3, 1)) in iv
    assert ("KEEP", date(1900, 1, 1), None) in iv
    mask = reference.membership_mask(list(r for r in reference.membership_intervals(["NEW", "KEEP"], changes)),
                                     pd.DatetimeIndex(["2019-01-02", "2021-01-04", "2025-01-02"]), ["OLD", "NEW", "GONE"])
    assert mask["OLD"].tolist() == [False, True, False] and mask["GONE"].tolist() == [True, False, False]


def test_calendar_sessions_holidays_and_early_close():
    at = lambda s: datetime.fromisoformat(s).replace(tzinfo=ET)  # noqa: E731
    assert cal.session_at(at("2026-11-26 12:00")) == cal.Session.CLOSED  # Thanksgiving
    assert cal.session_at(at("2026-11-27 12:30")) == cal.Session.REGULAR
    assert cal.session_at(at("2026-11-27 13:30")) == cal.Session.POST  # 1 pm early close
    assert cal.session_at(at("2026-11-23 08:00")) == cal.Session.PRE
    assert cal.session_at(at("2026-11-23 03:00")) == cal.Session.CLOSED
    assert cal.session_at(at("2026-11-23 20:30")) == cal.Session.CLOSED
    assert cal.last_completed_session(at("2026-11-23 12:00")) == date(2026, 11, 20)
    assert cal.last_completed_session(at("2026-11-23 16:01")) == date(2026, 11, 23)


def test_options_analytics_math():
    from app.market.analytics import compute_options

    exp = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    chain = [{"details": {"contract_type": t, "strike_price": k, "expiration_date": exp}, "greeks": {"gamma": 0.01}, "implied_volatility": iv,
              "open_interest": oi, "day": {"volume": vol}, "underlying_asset": {"price": 100, "ticker": "SPY"}}
             for t, k, iv, oi, vol in [("call", 100, 0.20, 1000, 500), ("put", 100, 0.22, 2000, 1000), ("call", 110, 0.18, 10, 5), ("put", 90, 0.25, 10, 5)]]
    r = compute_options(chain)
    assert r["put_call_volume"] == pytest.approx(1005 / 505) and r["put_call_oi"] == pytest.approx(2010 / 1010)
    assert r["gamma_exposure_usd_per_1pct"] == pytest.approx(0.01 * 100 * 100**2 * 0.01 * (1010 - 2010))
    assert 0.18 <= r["atm_iv_30d"] <= 0.25


def test_no_sample_or_fixture_path_in_production_code():
    from pathlib import Path

    app_dir = Path(__file__).parents[1] / "app"
    text = "\n".join(p.read_text() for p in app_dir.rglob("*.py"))
    for banned in ("sample-data", "load_sample", "tests/fixtures", "fixture_bars", "yahoo", "query1.finance"):
        assert banned not in text, banned
