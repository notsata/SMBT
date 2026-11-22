"""FRED / ALFRED (Federal Reserve Bank of St. Louis). With FRED_API_KEY: official API, first-release vintages for
revised series (true point-in-time), series metadata (last_updated, copyright notes). Without a key: the public
fredgraph CSV (latest vintage) plus a conservative publication lag; copyright status is then unknown."""

import io
from datetime import date

import pandas as pd

from ...config import get_settings
from ..http import http

API = "https://api.stlouisfed.org/fred"


def parse_observations(j: dict, first_release: bool) -> pd.DataFrame:
    rows = []
    for o in j.get("observations", []):
        if o.get("value") in (".", "", None):
            continue
        rows.append((date.fromisoformat(o["date"]), float(o["value"]), date.fromisoformat(o["realtime_start"]) if first_release else None))
    df = pd.DataFrame(rows, columns=["date", "value", "available_at"]).set_index("date")
    return df[~df.index.duplicated(keep="first")]


def parse_fredgraph(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    s = pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]).dt.date, dtype=float).dropna()
    return pd.DataFrame({"value": s, "available_at": None})


def metadata(series_id: str) -> dict:
    """{last_updated, title, units, frequency, copyrighted}. Needs a key."""
    j = http().json("fred", f"{API}/series", {"series_id": series_id, "api_key": get_settings().fred_api_key, "file_type": "json"})
    s = (j.get("seriess") or [{}])[0]
    notes = (s.get("notes") or "").lower()
    return {
        "last_updated": s.get("last_updated"),
        "title": s.get("title"),
        "units": s.get("units_short") or s.get("units"),
        "frequency": s.get("frequency_short"),
        "copyrighted": "copyright" in notes,
    }


def observations(series_id: str, revised: bool) -> tuple[pd.DataFrame, str]:
    key = get_settings().fred_api_key
    if key:
        params = {"series_id": series_id, "api_key": key, "file_type": "json"}
        if revised:
            params |= {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "output_type": 4}
        j = http().json("fred", f"{API}/series/observations", params)
        return parse_observations(j, revised), ("FRED/ALFRED API, first-release vintages" if revised else "FRED API")
    text = http().get("fred", "https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": series_id}).text
    return parse_fredgraph(text), "FRED download (latest vintage + publication lag)"
