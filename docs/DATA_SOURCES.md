# Data sources, subscriptions and licensing

All provider keys live in server environment variables (`.env`, container secrets, or `%LOCALAPPDATA%\SMBT\settings.env` for the Windows app). The browser never receives a key; it talks only to this server (`/api`, `/ws`). Nothing is stored on disk: history, macro series and live state are held in process memory and refetched after a restart. The full inventory and v1→v2 changes are in [DATA_AUDIT.md](DATA_AUDIT.md).

## 1. Providers

| Provider | What it supplies here | Plan needed | Env vars |
|---|---|---|---|
| **Alpaca Market Data** | Live trades/quotes/minute bars (WebSocket), snapshots, daily history from 2016, breadth constituent bars | Free (Basic): real-time **IEX** stream (one venue, ~2–3% of volume), 30-symbol stream cap, SIP history older than 15 min. Paid (Algo Trader Plus): real-time **SIP** (all US venues). `delayed_sip` = 15-min delayed consolidated stream. | `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_FEED` (`iex`\|`sip`\|`delayed_sip`) |
| **Massive** (formerly Polygon.io) | Live trades/quotes (real-time or 15-min delayed socket), snapshots, adjusted daily aggregates (history depth depends on plan), symbol search; optional VIX index stream (Indices plan) and option-chain snapshots (Options plan) | Stocks Starter or higher (delayed); Advanced for real-time; Indices and Options are separate products. Individual plans are licensed for personal use; customer-facing display needs a Business plan. | `MASSIVE_API_KEY`, `MASSIVE_REALTIME`, `MASSIVE_INDICES`, `MASSIVE_OPTIONS` |
| **Tiingo** | Long dividend-adjusted daily history (1993+) | Free tier works for benchmarks; paid for heavy use. Commercial display needs Tiingo's commercial licence. | `TIINGO_API_KEY` |
| **Financial Modeling Prep** | Company profile, market cap, sector; TTM ratios, key metrics, earnings, estimates, dividends, splits, ETF holdings; S&P 500 constituents and historical changes (for survivorship-free breadth); fallback search | Starter or higher for the endpoints used. Public display requires FMP's separate Data Display and Licensing Agreement. | `FMP_API_KEY` |
| **FRED / ALFRED** (St. Louis Fed) | 21 macro series (curve, rates, credit spreads, financial conditions, labour, inflation, liquidity, valuation) | Free. With a key: first-release vintages (true point-in-time). Without: public CSV download plus conservative publication lags. | `FRED_API_KEY` (optional) |
| **Cboe** | Official daily history for VIX, VIX3M, VIX9D, VVIX | None (public CSV files) | — |
| **Anthropic** | AI analyst | API key | `ANTHROPIC_API_KEY`, `ANALYST_MODEL` |

**Minimum useful setups**

| Goal | Keys |
|---|---|
| Live dashboard for one person, free | Alpaca (IEX) + Tiingo + (optional) FRED key |
| Full live terminal incl. breadth, fundamentals, VIX live, options | Massive (Stocks Advanced + Indices + Options) or Alpaca SIP, plus FMP, Tiingo, FRED key |
| No keys at all | Only Cboe VIX history and FRED macro load; every equity view returns "unavailable" with the reason. |

Run `python -m app.cli check` (in `backend/`) where the keys are set. It fetches from every configured provider, opens each stream and waits for real messages, and exits non-zero on any failure.

## 2. Licensing and access models

Exchange data licensing, not engineering, decides who may see real-time prices:

- **Real-time consolidated (SIP) and exchange data** shown to other people requires a redistribution licence and, per viewer, exchange display fees (CTA/UTP non-professional or professional). No retail API plan includes this for anonymous public visitors.
- **IEX**: real-time IEX data requires IEX's Data Subscriber Agreement; IEX data delayed by at least 15 minutes may be redistributed without IEX fees. Your vendor's own terms (Alpaca) still apply on top.
- **Massive**: individual plans are for personal use; showing data to customers or the public needs a Business plan.
- **FMP**: displaying FMP data publicly needs FMP's Data Display and Licensing Agreement; a paid plan alone does not cover it.
- **FRED**: FRED's terms grant no rights to third-party copyrighted series (e.g. ICE BofA, Moody's); those need the owner's permission for use beyond personal use. FRED's API terms also restrict storing, caching or archiving FRED content. This app keeps series in process memory only to compute the current view and never writes them to disk; review those terms for your deployment.

Because of this the server has two enforced modes:

| `ACCESS_MODE` | Who it is for | What is served |
|---|---|---|
| `personal` (default) | The operator's own use: the Windows app on 127.0.0.1, or a private server you do not share | Everything your keys allow, under your own subscriber agreements |
| `public` | Anonymous visitors on the internet | Only providers you list in `PUBLIC_DISPLAY_LICENSES` (your attestation that you hold display/redistribution rights for them), plus FRED public-domain series. A view whose core price data comes from an unlisted provider returns HTTP 451 with the reason; optional inputs from unlisted providers (macro, breadth, VIX) are dropped from derived scores rather than used. Copyrighted FRED series are excluded unless `fred-third-party` is listed. |

Examples of `PUBLIC_DISPLAY_LICENSES`: `massive` (Massive Business plan), `alpaca` (written permission from Alpaca for display), `fmp` (FMP display agreement), `cboe` (Cboe permission), `tiingo` (Tiingo commercial licence), `fred-third-party`.

A public deployment with no licences listed serves only FRED-derived macro views; that is the legally safe default, not a bug. The Data sources page shows the active mode and licence list.

## 3. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ACCESS_MODE` | `personal` | `personal` or `public` (see §2) |
| `PUBLIC_DISPLAY_LICENSES` | empty | Comma list of providers you are licensed to display publicly |
| `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` | empty | Alpaca credentials |
| `ALPACA_FEED` | `iex` | `iex` (free real-time, one venue), `sip` (paid real-time), `delayed_sip` (15-min delayed) |
| `MASSIVE_API_KEY` | empty | Massive credentials |
| `MASSIVE_REALTIME` | `false` | Plan includes real-time stocks (uses the real-time socket; else the delayed socket) |
| `MASSIVE_INDICES` | `false` | Plan includes Indices (live VIX) |
| `MASSIVE_OPTIONS` | `false` | Plan includes Options chain snapshots |
| `TIINGO_API_KEY` | empty | Tiingo credentials |
| `FMP_API_KEY` | empty | FMP credentials |
| `FRED_API_KEY` | empty | Enables ALFRED first-release vintages and faster change detection |
| `ANTHROPIC_API_KEY`, `ANALYST_MODEL` | empty, `claude-opus-5-5` | AI analyst |
| `STREAM_PROVIDERS` | `alpaca,massive` | Stream priority; failover only to another configured, licensed provider after 30 s down |
| `HISTORY_PROVIDERS` | `massive,tiingo,alpaca` | History priority; the provider covering `HISTORY_START` wins, one provider per series |
| `BENCHMARKS` | `SPY,QQQ` | Dashboard symbols (first is the default market benchmark) |
| `SCANNER_UNIVERSE` | built-in list of liquid ETFs and large caps | Comma list of tickers to scan |
| `HISTORY_START` | `1993-01-29` | Earliest history requested |
| `BREADTH_ENABLED` | `true` | Build S&P 500 breadth (needs FMP + Alpaca or Massive) |
| `MAX_SYMBOLS_PER_CLIENT` | `12` | Live symbols one browser connection may subscribe to |
| `UPSTREAM_UNSUBSCRIBE_GRACE_S` | `60` | Keep an upstream subscription this long after the last viewer leaves |
| `STALE_REGULAR_S`, `STALE_EXTENDED_S` | `120`, `1800` | No event for this long in regular / extended hours marks a symbol STALE |
| `SCORE_REFRESH_S`, `BREADTH_REFRESH_S` | `60`, `120` | Provisional score and intraday breadth refresh during sessions |
| `HTTP_MIN_INTERVAL_S` | `0.25` | Minimum spacing between requests to one provider host |
| `RATE_LIMIT_PER_MIN`, `HEAVY_RATE_LIMIT_PER_MIN`, `ANALYST_PER_HOUR` | `120`, `6`, `10` | Per-IP in-memory limits (general, backtests/signal reports, analyst) |
| `COMMISSION_BPS`, `SLIPPAGE_BPS` | `1.0`, `2.0` | Backtest costs per side |
| `FRONTEND_DIST` | `frontend/out` | Built PWA directory |
| Provider base URLs (`ALPACA_DATA_URL`, `ALPACA_STREAM_URL`, `MASSIVE_REST_URL`, `MASSIVE_WS_REALTIME_URL`, `MASSIVE_WS_DELAYED_URL`, `TIINGO_URL`, `FMP_URL`, `CBOE_URL`) | official endpoints | Override only for testing |

## 4. Reliability controls

- HTTP: per-host throttling, retries with backoff on 429/5xx/transport errors (honours `Retry-After`); keys never appear in errors or logs (httpx request logging is silenced because some providers take keys as query parameters).
- Streams: exponential backoff with jitter on disconnect; authentication or entitlement errors stop retries for 5 minutes and surface on the Data sources page; a watchdog reconnects a feed that goes silent while subscribed symbols should be trading.
- Events: dedup by (venue, trade id); trades older than the last one update volume/VWAP but not the last price; odd-lot, average-price and out-of-sequence conditions excluded from OHLC; late trades into closed minutes counted.
- Snapshot seeding at subscribe time, with a seed timestamp so stream events already contained in the snapshot are not double counted.
- History: completed sessions only; one provider per series (never stitched); invalid rows dropped and listed; if a refresh fails the last verified copy is served marked STALE, never silently.
- Failover: after 30 s of an unhealthy stream the hub reseeds the affected symbols from the next configured provider; data from two providers is never mixed within one symbol's session state.
