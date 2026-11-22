# SMBT — Market Top & Bottom Research Terminal

Installable PWA (iPhone + Windows/desktop) backed by a Python quant engine that answers one question:

> *Based on all currently available evidence and historically validated indicators, how attractive or dangerous is this market right now?*

It separates three kinds of evidence everywhere in the UI and API, and never mixes them:

| Kind | What it is | Example |
|---|---|---|
| **Heuristic score** | Regime-weighted composite of point-in-time indicator percentiles (0–100). Not a probability. | Bottom Opportunity 58, Top Risk 36 |
| **Empirical frequency** | Counted from history with episode-level CIs, validated walk-forward (Brier skill vs base rate). | "63% of past days in this bucket had a positive 60d return; base rate 64%; OOS Brier skill −0.09" |
| **Historical similarity** | Nearest past dates by feature fingerprint and what followed (distributions, not averages). | "Closest: 2011-09-30 (debt-ceiling correction), 2015-09-01…" |

| Desktop | iPhone |
|---|---|
| ![dashboard](docs/img/dashboard-desktop.png) | ![iphone](docs/img/dashboard-iphone.png) |

*(Screenshots use the bundled 1999–2018 sample data, so cards are marked stale.)*

## What's in it

- **Dashboard**: regime, Bottom Opportunity / Top Risk / Trend Confidence, VIX, breadth, reconstructed Fear & Greed, macro risk; each card shows as-of date, source, stale/unavailable state. Candlestick chart with historical signal markers — tap one to see which indicators triggered it and what happened next.
- **Indicator engine** (all causal, truncation-tested): RSI + divergences, MACD + divergences, Stoch RSI, Bollinger, 20/50/100/200 MAs, distance from 200d, anchored VWAP, ATR, ADX, volume profile, OBV/volume-price divergence, historical vol, relative strength.
- **Breadth** from S&P 500 members: % above 20/50/200d, A/D line, McClellan Oscillator & Summation, new highs−lows, Zweig Breadth Thrust, % oversold/overbought, 90% down days, breadth divergence at highs; optional point-in-time membership to remove survivorship bias.
- **Sentiment & macro**: VIX & term structure, put/call, AAII/NAAIM (import), F&G reconstruction; yield curve (+ un-inversion), Fed funds, real yields, credit spreads, NFCI, Sahm gap, CPI/core CPI, Philly Fed manufacturing, net liquidity, M2, market cap/GDP, CAPE (import) — all **point-in-time** (ALFRED first-release vintages or conservative publication lags).
- **Validation**: walk-forward parameter selection, purged time-series CV, costs + slippage, next-open execution, block-bootstrap CIs vs buy & hold (SPY/QQQ), bull/bear split, event studies vs random entries with Benjamini–Hochberg FDR, turning-point precision/recall/lag.
- **Analyze any ticker**: normal pullback vs possible accumulation vs structural decline vs potential exhaustion, with forward-return stats for that state on that symbol, sector and relative strength.
- **Scanner**: 9 setup types across the universe (daily/weekly), ranked by pooled historical evidence; filter by type, cap, sector, signal, ticker.
- **Historical analogs**: closest periods incl. dot-com, GFC, COVID, 2022, with 5–252 day outcome distributions.
- **Alerts**: score crosses, VIX range, breadth extremes, divergences, regime changes, watchlist setups — edge-triggered with hysteresis, cooldowns and a daily cap; Web Push to iPhone (16.4+, installed) and desktop.
- **AI analyst** (Claude): answers only from tool calls into the app's own computed data, cites each tool/as-of date, presents bull and bear evidence.
- **PWA**: installable, full-screen, bottom nav on phones, sidebar + keyboard shortcuts on desktop (`?`), dark/light, offline access to previously loaded data (clearly labelled as cached).

## Measured, not claimed

`python -m app.cli report <SYMBOL>` prints the signal scoreboard and walk-forward results from stored data. On the bundled sample (S&P 500 and NASDAQ Composite, 1999–2018) **no timing strategy beat buy-and-hold CAGR out of sample**, and the one signal with a significant S&P 500 event-study edge (Bottom score ≥ 70, p = 0.015, q = 0.09, 20 events) **did not replicate on the NASDAQ** (p = 0.42). Full numbers and caveats: [docs/METHODOLOGY.md](docs/METHODOLOGY.md#measured-results-on-the-bundled-sample). Re-run on live data after ingestion — results will change, and the app shows whatever the data says.

## Quick start (local, no API keys)

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m app.cli sample          # real 1999-2018 index data bundled in sample-data/
python -m app.cli ingest          # optional: live data (needs internet)
uvicorn app.main:app --reload     # http://localhost:8000/api/...

# frontend (separate terminal)
cd frontend && npm install && npm run dev   # http://localhost:3000, proxies /api to :8000
```

Tests: `cd backend && python -m pytest` (74 tests; also run against PostgreSQL in CI) · `cd frontend && npm test && npx tsc --noEmit`.

Deploy (Docker Compose + TimescaleDB + HTTPS, iPhone install, push setup): [docs/DEPLOY.md](docs/DEPLOY.md).
Data providers, delays and licensing limits: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Layout

```
backend/app/
  data/        providers (Yahoo, Tiingo, Cboe, FRED/ALFRED), ingestion, quality checks, point-in-time store
  quant/       technical, breadth, macro/sentiment, scores & regime, probabilities, analogs
  backtest/    engine (next-bar execution, costs), strategies, validation (walk-forward, CV, events, pivots)
  scanner.py alerts.py analyst.py services.py main.py cli.py
frontend/      Next.js 16 static export (App Router, Tailwind 4, lightweight-charts 5), service worker, manifest
sample-data/   real historical data for offline development (see sample-data/README.md)
```

Research software, not investment advice.
