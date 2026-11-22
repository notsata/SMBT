# Data sources

All data is **end-of-day** unless stated. Nothing is real-time; the UI labels EOD data with its as-of date and any delayed quote with its delay. Missing or stale data is shown as unavailable/stale — never filled with invented values.

| Data | Provider | Endpoint | Key | Frequency / delay | Notes |
|---|---|---|---|---|---|
| Stock/ETF daily OHLCV (split+dividend adjusted) | Tiingo | `api.tiingo.com/tiingo/daily/{sym}/prices` | `TIINGO_API_KEY` (free tier) | EOD, ~17:30 ET | Preferred when configured. Free tier rate limits (≈1000 req/day) are too tight for 500 breadth names daily. |
| Same (fallback) and delayed last quote | Yahoo Finance chart API | `query1.finance.yahoo.com/v8/finance/chart/{sym}` | none | EOD bars; quote delayed ~15 min | Unofficial and unsupported; terms may restrict use. In-progress session never stored. |
| VIX, VIX3M, VIX9D, VVIX | Cboe | `cdn.cboe.com/api/global/us_indices/daily_prices/{NAME}_History.csv` | none | EOD | Official index history. |
| Equity / total put/call ratio | Cboe (archived) | `cdn.cboe.com/resources/options/volume_and_call_put_ratios/{equity,total}pc.csv` | none | daily, 2006–2019 | Cboe stopped the free daily CSV; import a licensed file (`PCR_EQUITY`) for current data. |
| Yield curve (T10Y2Y, T10Y3M), DFF, DGS10, DFII10, DTB3, BAA10Y, HY OAS, NFCI, UNRATE, ICSA, CPI, core CPI, Philly Fed mfg, WALCL, WTREGEN, RRPONTSYD, M2, Z.1 equities, GDP, Moody's AAA/BAA | FRED / ALFRED (St. Louis Fed) | `api.stlouisfed.org/fred/series/observations` (with key) or `fred.stlouisfed.org/graph/fredgraph.csv` | `FRED_API_KEY` optional | daily → quarterly | With a key, revised series use first-release vintages (true point-in-time). Without, a conservative publication lag per series (`data/catalog.py`). ICE BofA HY OAS history on FRED may be licence-limited; BAA10Y is the long-history credit fallback. |
| ISM Manufacturing PMI | — | — | — | — | Licensed, removed from FRED. **Substitute:** Philadelphia Fed manufacturing index (GACDFSA066MSFRBPHI). |
| S&P 500 current members | datasets/s-and-p-500-companies (GitHub, from Wikipedia) | `raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv` | none | updated irregularly | Breadth universe. |
| S&P 500 historical membership | user-provided URL (e.g. fja05680/sp500) | `SP500_HISTORY_URL` (CSV `date,tickers`) | none | change dates | Removes survivorship bias in who counts on each date. |
| AAII sentiment, NAAIM exposure, short interest, Shiller CAPE | user import | `POST /api/admin/import/{SERIES}` (CSV `date,value[,available_at]`) | — | weekly / monthly | AAII history requires membership; NAAIM publishes on naaim.org; CAPE from Shiller's dataset. |
| Fear & Greed | reconstruction | computed | — | daily | Follows CNN's published 7-input design with the inputs available here; not CNN's index. |
| Gamma exposure, unusual options activity | — | — | — | — | **Unavailable**: needs licensed OPRA-level options data. |
| Earnings revisions / forward EPS | — | — | — | — | **Unavailable**: needs FactSet / LSEG I/B/E/S or similar. |
| NYSE advance/decline | — | — | — | — | **Unavailable** from a free official source; breadth uses S&P 500 members instead. |

## Reliability controls

- HTTP client: per-host throttling (`HTTP_MIN_INTERVAL_S`), exponential backoff with jitter on 429/5xx/transport errors, `Retry-After` honoured; API keys are never included in error messages or the ingest log.
- Provider fallback: Cboe → (Tiingo) → Yahoo per symbol; each bar row records its source.
- Re-fetch overlap of 10 days on every run picks up late corrections; upserts are idempotent.
- Quality checks (`data/quality.py`): duplicate dates, non-positive prices dropped, inconsistent high/low → NaN, >50% daily moves flagged for split review, negative volume removed. Issues land in `ingest_runs` and on the Settings page.
- Staleness: a series is flagged stale when its last bar is older than `STALE_AFTER_DAYS` (default 4 calendar days, covering weekends/holidays).
- Point-in-time store: every macro/sentiment observation has `available_at`; reads go through `pit_series`.
