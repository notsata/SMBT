# Sample data

Real historical data for development and tests without network access. Extracted from the datasets bundled in the
[`arch`](https://pypi.org/project/arch/) Python package (v9.0.0, NCSA licence), which were originally exported from
Yahoo Finance and FRED.

| File | Content | Range |
|---|---|---|
| `bars_gspc.csv.gz` | S&P 500 index (^GSPC) daily OHLCV | 1999-01-04 – 2018-12-31 |
| `bars_ixic.csv.gz` | NASDAQ Composite (^IXIC) daily OHLCV | 1999-01-04 – 2018-12-31 |
| `bars_vix.csv.gz` | Cboe VIX daily close (holidays removed) | 2014-01-03 – 2019-01-03 |
| `macro.csv.gz` | Moody's AAA & BAA yields (monthly), core CPI (CPILFESL, monthly) | 1919/1957 – 2018 |

Loaded with `python -m app.cli sample`; rows are labelled `sample: arch package bundle …` everywhere they appear.

Not included (needs live ingestion): SPY/QQQ/ETFs/stocks, S&P 500 constituents (so breadth is unavailable in sample mode),
VIX3M, put/call, FRED daily series, 2019+ history (COVID, 2022). Macro sample values have no vintage history, so a
conservative publication lag is applied.
