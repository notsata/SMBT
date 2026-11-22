import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.data import providers as P
from app.data.quality import clean_bars, is_stale


def test_yahoo_parser_drops_in_progress_session():
    tz = ZoneInfo("America/New_York")
    d1, d2 = datetime(2026, 11, 19, 9, 30, tzinfo=tz), datetime(2026, 11, 20, 9, 30, tzinfo=tz)
    end = datetime(2026, 11, 20, 16, 0, tzinfo=tz)
    j = {"chart": {"error": None, "result": [{
        "meta": {"exchangeTimezoneName": "America/New_York", "regularMarketPrice": 101.5, "regularMarketTime": int(end.timestamp()) - 600,
                 "currentTradingPeriod": {"regular": {"end": int(end.timestamp())}}},
        "timestamp": [int(d1.timestamp()), int(d2.timestamp())],
        "indicators": {"quote": [{"open": [99, 100], "high": [101, 102], "low": [98, 99], "close": [100, 101.5], "volume": [1e6, 5e5]}],
                       "adjclose": [{"adjclose": [99.5, 101.5]}]}}]}}
    during = datetime(2026, 11, 20, 14, 0, tzinfo=tz)
    df, q = P.parse_yahoo_chart(j, now=during)
    assert list(df.index) == [date(2026, 11, 19)]  # live bar excluded
    assert q["price"] == 101.5 and "delayed" in q["delay"]
    after = datetime(2026, 11, 20, 17, 0, tzinfo=tz)
    df2, _ = P.parse_yahoo_chart(j, now=after)
    assert list(df2.index) == [date(2026, 11, 19), date(2026, 11, 20)]
    assert df2["adj_close"].iloc[0] == 99.5


def test_cboe_and_fred_parsers():
    vix = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.24,17.24,17.24,17.24\n01/03/1990,18.19,18.19,18.19,18.19\n"
    df = P.parse_cboe_history(vix)
    assert df.index[0] == date(1990, 1, 2) and df["close"].iloc[1] == 18.19
    vvix = "DATE,VVIX\n01/03/2007,87.63\n"
    assert P.parse_cboe_history(vvix)["close"].iloc[0] == 87.63
    pc = "CBOE EQUITY PUT/CALL RATIO\nsome disclaimer\nDATE,CALL,PUT,TOTAL,P/C Ratio\n11/1/2006,976510,623929,1600439,0.64\n11/2/2006,1,1,2,0.7\n"
    s = P.parse_cboe_putcall(pc)
    assert s.loc[date(2006, 11, 2)] == 0.7
    g = P.parse_fredgraph("observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,3.99\n")
    assert len(g) == 2 and g.iloc[-1] == 3.99
    j = {"observations": [{"date": "2024-01-01", "value": "3.7", "realtime_start": "2024-02-02"}, {"date": "2024-02-01", "value": ".", "realtime_start": "2024-03-08"}]}
    o = P.parse_fred_observations(j, first_release=True)
    assert len(o) == 1 and o["available_at"].iloc[0] == date(2024, 2, 2)


def test_tiingo_parser():
    rows = [{"date": "2024-01-02T00:00:00.000Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "adjClose": 1.4}]
    df = P.parse_tiingo(rows)
    assert df.index[0] == date(2024, 1, 2) and df["adj_close"].iloc[0] == 1.4


def test_membership_intervals():
    snaps = pd.DataFrame({"date": ["2020-01-01", "2020-06-01", "2021-01-01"], "tickers": ["A,B,C", "A,C,D", "A,D"]})
    iv = {(r["symbol"], str(r["start_date"]), str(r["end_date"])) for r in P.membership_intervals(snaps)}
    assert ("B", "2020-01-01", "2020-05-31") in iv
    assert ("C", "2020-01-01", "2020-12-31") in iv
    assert ("D", "2020-06-01", "None") in iv


def test_quality_checks_flag_without_inventing():
    idx = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    df = pd.DataFrame({"open": [10, -1, 10, 10], "high": [11, 11, 9, 11], "low": [9, 9, 9.5, 9], "close": [10.5, 10, 10, 0], "adj_close": [10.5, 10, 10, 0], "volume": [1, 1, 1, 1]}, index=idx)
    out, issues = clean_bars(df)
    assert len(out) == 3  # zero close dropped, not filled
    assert pd.isna(out.loc[date(2024, 1, 3), "open"])
    assert pd.isna(out.loc[date(2024, 1, 4), "high"])  # high below close -> invalid
    assert issues
    assert is_stale(date(2024, 1, 1), today=date(2024, 1, 10)) and not is_stale(date(2024, 1, 9), today=date(2024, 1, 10))


def test_http_retries_then_fails(monkeypatch):
    import httpx

    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503)

    monkeypatch.setattr(P.time, "sleep", lambda s: None)
    h = P.Http(0, attempts=3, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(P.DataError) as e:
        h.get("https://example.test/x", params={"api_key": "SECRET"})
    assert calls["n"] == 3 and "SECRET" not in str(e.value)


def test_point_in_time_series_uses_publication_date(db):
    from app.data.store import pit_series
    from app.db import SeriesObs, upsert

    upsert(db, SeriesObs, [
        {"series_id": "TEST", "date": date(2020, 1, 1), "value": 1.0, "available_at": date(2020, 2, 7), "source": "t"},
        {"series_id": "TEST", "date": date(2020, 2, 1), "value": 2.0, "available_at": date(2020, 3, 6), "source": "t"},
    ], ["series_id", "date"])
    db.commit()
    idx = pd.DatetimeIndex(["2020-02-06", "2020-02-07", "2020-03-05", "2020-03-06"])
    v = pit_series(db, "TEST", idx)["value"].tolist()
    assert pd.isna(v[0]) and v[1:] == [1.0, 1.0, 2.0]
    yo = pit_series(db, "TEST", idx, transform=lambda s: s.diff())["value"].tolist()
    assert pd.isna(yo[2]) and yo[3] == 1.0  # derived value waits for its newest input


def test_sample_data_loaded(db):
    from app.data.store import load_bars

    b = load_bars(db, "^GSPC")
    assert b.index[0].year == 1999 and b.index[-1].year == 2018 and len(b) > 5000
    assert "sample" in b.attrs["source"]
