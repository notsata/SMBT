# Methodology

## 1. No look-ahead, by construction and by test

| Risk | Control | Where |
|---|---|---|
| Indicators using future bars | Only rolling/expanding/EWM windows. Percentiles are *expanding* ranks (value vs its own history up to today, min 252 obs). | `quant/technical.py` |
| Pivot-based signals (divergences) | Pivot needs k bars on each side; the signal is stamped on the **confirmation bar** (pivot + k), never the pivot bar. | `technical.divergences` |
| Macro revisions / publication delay | Every observation stores `available_at`. With `FRED_API_KEY`, revised series use ALFRED **first-release** vintages (`output_type=4`); otherwise a conservative per-series lag (e.g. CPI +46 days from period start). Derived values (YoY, Sahm gap) only become usable when their newest input was published; availability is forced monotone. | `data/catalog.py`, `data/store.pit_series` |
| Partial intraday bars | The in-progress session is never stored; only settled daily bars. | `providers.parse_yahoo_chart` |
| Same-bar execution | A target decided at close *t* is filled at the **open of t+1** (or close of t+1 if no opens) and earns returns from there. | `backtest/engine.py` |
| Parameter snooping | Walk-forward: parameters chosen on the train window only; OOS segments concatenated. Purged k-fold excludes 126 sessions before and 20 after each test block. | `backtest/validation.py` |
| Unresolved labels in probability estimates | At date *t*, only outcomes whose window closed by *t* (index ≤ t−h) are counted. | `quant/probability.walk_forward_probability` |

**Truncation-invariance tests** (`tests/test_indicators.py`, `tests/test_backtest.py`): technical features, breadth, scores, regime and every strategy's target weights computed on `data[:t]` must equal the same values computed on all data. A future-peeking change fails CI.

Future prices appear in exactly two places, both evaluation-only: forward-return labels and zigzag pivots used to grade turning-point accuracy.

## 2. Scores (heuristic)

Each component maps to 0–1 (point-in-time percentile, scaled level, or a recent-event flag). Category means are combined with **regime-dependent weights**; categories with no data are dropped and weights renormalised; the score is withheld below 25% component coverage. Coverage is shown on every card.

| Category | Bottom Opportunity components | Top Risk components |
|---|---|---|
| Technical | RSI(14) low, drawdown depth, distance below 200d, Bollinger %B low, bullish RSI/MACD divergence (10d), capitulation volume (5d) | extension above 200d, RSI high, stretch above 50d in ATRs, bearish divergence near highs, price up on falling OBV |
| Breadth | few members above 50d, share oversold, McClellan low, net new lows, Zweig thrust (20d), 90% down day (10d) | breadth divergence at highs, share overbought, weak net new highs near index high |
| Sentiment | VIX high, VIX/VIX3M backwardation, put/call high, AAII bearish, F&G fear, realised vol high | VIX low, steep contango, put/call low, AAII bullish, F&G greed |
| Macro | credit stress high but easing | curve un-inverting, credit widening, Fed tightening, Sahm gap, valuation (CAPE or market cap/GDP) |

Weights (technical/breadth/sentiment/macro), e.g. Bottom in *strong bull* 0.50/0.20/0.25/0.05 (dips are shallow technical events) vs *bear capitulation* 0.20/0.35/0.35/0.10 (washouts are breadth/sentiment events). Full tables: `quant/scores.py`.

**Trend Confidence**: mean of ADX strength, MA stack alignment (close/20/50/100/200 in trend direction), 63-day log-price R², persistence on the trend side of the 50d, breadth confirmation. Direction reported separately.

**Regime** (first rule that matches): bear capitulation (drawdown ≤ −20% and VIX ≥ 90th pct or RSI < 30 or ≥ 40% members oversold or ≤ 15% above 50d) → recovery (from a bear state within 126 sessions, back above a rising 50d, 20d return > 4%) → early bear (below a flat/falling 200d, drawdown ≤ −10%) → distribution (near highs, extension ≥ 85th pct, plus a divergence or breadth warning) → strong bull → bull correction (−5% to −20%) → sideways.

## 3. Empirical probabilities

For the headline panel the current score's 10-point bucket is compared with all past days in that bucket:

- Bottom → P(positive return over 20/60/126 sessions); Top → P(≥10% drawdown within 60/126/252 sessions), plus base rates.
- **Episodes**: qualifying days are clustered (new episode after a gap > horizon); CIs resample whole episodes (cluster bootstrap), because overlapping windows are not independent. Fewer than 8 episodes is flagged "thin".
- **Out-of-sample check**: the bucket estimate is computed walk-forward with purged labels and scored against realised outcomes; Brier skill vs the expanding base rate is shown next to every probability. Negative skill means the score added no predictive information for that event — the UI says so.

## 4. Historical analogs

Fingerprint = point-in-time percentiles of momentum (dist200, 63d ROC), drawdown, breadth, sentiment (VIX, term structure, put/call, F&G), volatility, valuation, credit (level, 63d change), liquidity (net liquidity 63d change), rates (curve, real yield). Similarity = 1 − RMS distance over shared features (≥ 60% overlap). Matches exclude the last 126 sessions and are de-clustered ≥ 40 sessions apart. Output: forward 5–252 session returns per match, outcome percentiles across matches vs all days, and similarity to 13 named turning points (2000, 2002, 2007, 2009, 2011, 2016, 2018, 2020, 2022, 2025).

## 5. Backtests and signal statistics

- **Costs**: commission + slippage bps per side on every weight change (defaults 1 + 2 bps). Cash earns the point-in-time 3M T-bill when loaded, else 0% (stated in the result).
- **Metrics**: CAGR, vol, Sharpe/Sortino (excess of T-bill), max drawdown, Calmar, exposure, trades, win rate, avg trade, profit factor; split by market state (benchmark above/below its 200d).
- **Benchmark comparison**: paired moving-block bootstrap (block 20, 1000 resamples) CI for Sharpe difference and annualised return difference vs buy & hold of the same asset; SPY and QQQ buy & hold also reported when loaded.
- **Event studies**: rising-edge events de-clustered by a cooldown; entry next open, exit close at h. p-value = share of 2000 random same-size entry sets (same period) that did at least as well (one-sided: bullish signals should beat random, bearish should lag). **Benjamini–Hochberg q-values** across all signals on the scoreboard; "robust" requires q < 0.10.
- **Turning points**: zigzag pivots (default 10% swings) graded after the fact. Precision = share of signals within ±20 sessions of a pivot of the right kind; recall = share of pivots with a signal nearby; median lead/lag; missed pivots listed.
- **Stability**: event-study excess per contiguous era (5 blocks) — an edge from a single era is visible as such.
- **Survivorship**: index/ETF return series are not affected by constituent survivorship. Breadth is computed over current members unless `SP500_HISTORY_URL` provides membership history; the API flags which. Scanner evidence pools today's universe and says so.

## Measured results on the bundled sample

Data: S&P 500 (^GSPC) and NASDAQ Composite (^IXIC) daily 1999-01-04 → 2018-12-31; VIX only from 2014; no breadth, no FRED daily series (so these scores run at ~45–55% coverage). Generated by `python -m app.cli report ^GSPC` / `^IXIC`. Walk-forward: 6y train / 2y test, OOS 2006-04 → 2018-12, 1 + 2 bps.

**Signal scoreboard (60-session forward, next-open entry)** — top rows:

| Signal | ^GSPC n | excess | p | q | ^IXIC n | excess | p | q |
|---|---|---|---|---|---|---|---|---|
| Bottom score ≥ 70 | 20 | +3.5% | 0.015 | 0.09 | 9 | +0.8% | 0.42 | 0.85 |
| 50d crosses above 200d | 8 | +5.5% | 0.010 | 0.09 | 11 | +4.0% | 0.08 | 0.45 |
| RSI(14) < 30 | 24 | +2.0% | 0.069 | 0.28 | 28 | +3.1% | 0.058 | 0.45 |
| Top risk ≥ 70 (bearish) | 27 | −0.7% | 0.32 | 0.65 | 14 | +0.3% | 0.55 | 0.85 |
| Bearish RSI divergence | 40 | −0.5% | 0.33 | 0.65 | 38 | +1.0% | 0.70 | 0.85 |

**Walk-forward OOS vs buy & hold**

| Strategy | ^GSPC CAGR (B&H 5.2%) | Sharpe (B&H 0.37) | MaxDD (B&H −57%) | ΔSharpe 95% CI | ^IXIC CAGR (B&H 8.4%) | ΔSharpe 95% CI |
|---|---|---|---|---|---|---|
| 200-day trend filter | 5.9% | 0.61 | −21% | [−0.25, +0.68] | 4.5% | [−0.58, +0.35] |
| Top-risk de-risking | 3.7% | 0.29 | −57% | [−0.26, +0.11] | 7.7% | [−0.17, +0.14] |
| Combined score overlay | 4.3% | 0.32 | −57% | [−0.14, +0.05] | 7.8% | [−0.14, +0.11] |
| Bottom-score entries | 1.7% | 0.21 | −24% | [−0.65, +0.25] | 0.6% | [−0.85, +0.05] |
| RSI(2) mean reversion | 2.4% | 0.40 | −15% | [−0.55, +0.60] | 2.0% | [−0.72, +0.55] |

Reading: no strategy's Sharpe improvement is statistically distinguishable from zero; the trend filter roughly halved drawdowns at similar return on the S&P but not on the NASDAQ; the top-risk score did not anticipate 10% corrections better than the base rate (negative OOS Brier skill). The bottom-score entry edge on the S&P did not replicate on the NASDAQ. These results include no breadth or credit-spread inputs — re-run after live ingestion before drawing conclusions.

## Known limitations

- Breadth uses S&P 500 members, not the NYSE tape; history is survivorship-biased without a membership file, and delisted constituents' prices are not available from free sources even with one (coverage is reported).
- Free price data (Yahoo, unofficial) can have gaps and adjustment errors; quality checks flag but do not repair them. Prefer Tiingo or a paid vendor for production.
- Put/call daily history from Cboe ends in 2019; AAII, NAAIM, CAPE, short interest require import.
- Gamma exposure, unusual options activity, earnings revisions and company fundamentals need licensed data and are shown as unavailable.
- Score weights are a priori, not fitted (fitting them would need its own walk-forward). Their usefulness is exactly what the validation pages measure.
