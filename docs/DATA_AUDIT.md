# Data-source audit (v1 → v2)

Every data dependency of the terminal, where it came from in v1, where it comes from now, and how it is labelled.
Status labels are defined in [METHODOLOGY.md §0](METHODOLOGY.md#0-data-status-labels).

## What v1 did that v2 removes

| v1 behaviour | Problem | v2 |
|---|---|---|
| `sample-data/*.csv.gz` bundled into the Docker image, Windows installer and a "load sample data" button | Historic 1999–2018 data could be mistaken for current data | Moved to `backend/tests/fixtures/` (test-only). No production code path can read it (`test_providers.py::test_no_sample_or_fixture_path_in_production_code` enforces this). |
| Postgres/SQLite store of prices, macro series, watchlists, alert rules, push subscriptions, sessions | Persistent market database and persistent user data | Removed (`app/db.py`, `app/data/*`). History and macro are fetched on demand and held in process memory with TTLs. |
| Yahoo chart API as keyless price fallback | No licence for redistribution; unofficial endpoint | Removed. Only licensed APIs (Massive, Alpaca, Tiingo) supply equity prices. |
| Scheduled ingestion worker, end-of-day only | Dashboard was always one day behind; no intraday data | Replaced by on-demand REST history plus WebSocket streaming. |
| `APP_ACCESS_TOKEN` login, cookies | Accounts/login were not wanted | Removed; the terminal is anonymous. |
| Synced watchlist, server-stored alert rules, Web Push (VAPID) | Persistent per-user data | Watchlist and alert rules live in the browser tab's memory and on its WebSocket connection; alerts show as local notifications while the page is open. No push subscriptions are stored. |
| Service worker cached `/api` responses for offline use | Market data persisted on device; stale data shown offline | Service worker caches only the static app shell; v2 deletes v1's data cache on activation. |
| `localStorage` for theme and dashboard symbol | Persistent preferences | Theme follows the system and can be changed for the tab only; symbol is React state. |
| Manual CSV import for AAII, NAAIM, put/call, short interest, CAPE | User-supplied data with unknown provenance | Removed; listed as unavailable (below). |
| Cboe put/call CSV archive (ended 2019) carried forward into scores | Stale input silently used for current scores | Removed from scores. Live SPY/QQQ options put/call (Massive Options) is shown separately and not used in scores (no history). |
| Macro series carried forward indefinitely after the last observation | Stale values entered scores silently | Each series has `max_age_days`; beyond it the value is excluded and the card shows STALE with the series counts. |
| Static mobile snapshot artifact (`packaging/artifact`, JSON exported from the sample DB) | Static data presented as a terminal | Replaced by a one-page artifact that reads the viewer's own Twelve Data (or Alpha Vantage) connector at view time (no bundled data); its JavaScript indicators and state rules are checked against the server's Python in `tests/test_pocket.py`. |
| `^GSPC`/`^IXIC` Yahoo index history as default benchmarks | Index levels are licensed separately from ETF trades | Default benchmarks are SPY and QQQ (exchange-traded, covered by equity feeds). |
| Wikipedia S&P 500 constituent CSV, optional user membership CSV | Unofficial, survivorship-biased | FMP constituents plus historical changes → point-in-time membership intervals. |

## Inventory (v2)

| Data | Provider (priority order) | Transport | Status label | Notes |
|---|---|---|---|---|
| Last trade, bid/ask, day OHLC/VWAP | Alpaca (IEX real-time, SIP real-time, or 15-min delayed SIP), Massive (real-time or 15-min delayed) | WebSocket, seeded by REST snapshot | LIVE / DELAYED / STALE / CLOSED / UNAVAILABLE per symbol | Dedup by (venue, trade id), out-of-order ignored for last price, odd-lot/average-price conditions excluded from OHLC. IEX volume is single-venue and labelled so. |
| 1-minute bars (today) | Same stream (aggregated from trades, provisional) → provider's official minute bar replaces it | WebSocket + REST | Same as quote | Late trades counted; provisional bars flagged `final=false`. |
| Daily OHLCV history | Massive (aggregates, adjusted), Tiingo (dividend-adjusted), Alpaca (adjustment=all, 2016+) | REST on demand | Confirmed closes; STALE if provider is behind the last completed session | One provider per series (no stitching); completed sessions only; validation drops bad rows. |
| VIX, VIX3M, VIX9D, VVIX history | Cboe official CSV files | REST (keyless) | DELAYED (prior close) / CLOSED | Live VIX value only with Massive Indices. |
| VIX live value | Massive Indices (`V.I:VIX`) | WebSocket | LIVE / DELAYED | |
| Macro (21 FRED series) | FRED API (ALFRED first-release vintages with `FRED_API_KEY`), else fredgraph CSV with publication lags | REST, refreshed when FRED's `last_updated` changes | DELAYED (official release schedule) / STALE beyond `max_age_days` | Copyrighted third-party series are tagged and withheld in public mode unless licensed. |
| S&P 500 membership | FMP constituents + historical changes | REST | — | Membership intervals remove survivorship bias from breadth. |
| Breadth (% above 50/200-day, McClellan, new highs/lows) | Constituent daily bars (Alpaca or Massive bulk) + intraday snapshots | REST, recomputed every 10 min; intraday every 120 s in session | DELAYED (polled) / confirmed | Needs FMP + Alpaca/Massive. |
| Company profile, market cap, sector | FMP | REST, 24 h in memory | — | |
| Fundamentals (TTM ratios, key metrics, earnings, estimates, dividends, splits, ETF holdings) | FMP | REST, 12 h in memory | Reported values with provider name | |
| Options put/call (volume, OI), ATM IV, dealer gamma | Massive Options chain snapshot | REST on request | DELAYED / LIVE per plan | SPY/QQQ and ETFs; not used in scores (no history). |
| Symbol search | Massive, then FMP | REST, 1 h in memory | — | |
| Trading calendar, sessions, early closes | `exchange_calendars` (XNYS) | Library | — | Pre-market 04:00, regular, after-hours to close + 4 h. |
| Scores, regime, probabilities, analogs, backtests | Derived | — | Confirmed (closes) or provisional (with today's live bar) | Validation and probabilities use confirmed data only. |
| AI analyst | Anthropic Messages API | REST | — | Tool calls read the same live layer; answers cite tool, symbol and as-of. |

## Unavailable by design

Shown in the app under "Not shown (no licensed or trustworthy source)":
NYSE advance/decline (no free official source), Cboe market-wide put/call (web pages only; free history ended 2019), AAII sentiment (membership), NAAIM exposure (spreadsheet only), FINRA short interest (not integrated), Shiller CAPE (no API; market cap / GDP from FRED is used), unusual options activity (needs a licensed options trade feed), 13F positioning, point-in-time estimate revisions (licensed consensus data).

## What remains bundled

Only `backend/tests/fixtures/` (real 1999–2018 index and macro history used to exercise the analysis code in tests through the provider seam). It is not copied into the Docker image or the Windows installer; CI checks the image for it.

## Verification

| Check | Where | Result in this environment |
|---|---|---|
| Parsers for every provider's documented payloads | `tests/test_providers.py` | pass |
| Hub rules: dedup, out-of-order, conditions, sessions, seed, failover, status | `tests/test_stream.py` | pass |
| Upstream reconnect with backoff; auth failure → fatal state | `tests/test_stream.py` (local fake WebSocket servers) | pass |
| Fake Alpaca WebSocket + REST → hub → `/ws` | `tests/test_e2e_live.py` | pass |
| Built PWA in Chromium, desktop and iPhone 13 viewports: status badges, inspector, streamed price changes on its own, honest error without history, session-only alerts, no `localStorage`/`sessionStorage`/IndexedDB/cookies/cached `/api` | `tests/test_browser.py` + `frontend/tests/browser.e2e.mjs` | pass |
| API: provenance fields, licence refusal (451), rate limits, WS protocol, no persistence | `tests/test_api.py` | pass |
| Docker image runs read-only with no keys; Cboe VIX fetched live; `/api/overview` refuses (503) without an equity provider | `.github/workflows/ci.yml` `docker` job | runs in CI |
| Real provider APIs and streams | `python -m app.cli check` with keys set | **not run here**: this sandbox's network blocks provider hosts and no keys were supplied. Run it where the keys are (see DATA_SOURCES.md). |
