# Test fixtures

Real historical data used only by the test suite (never by the running app, which has no bundled data). Extracted from the datasets bundled in the
[`arch`](https://pypi.org/project/arch/) Python package (v9.0.0, NCSA licence), which were originally exported from
Yahoo Finance and FRED.

| File | Content | Range |
|---|---|---|
| `bars_gspc.csv.gz` | S&P 500 index (^GSPC) daily OHLCV | 1999-01-04 – 2018-12-31 |
| `bars_ixic.csv.gz` | NASDAQ Composite (^IXIC) daily OHLCV | 1999-01-04 – 2018-12-31 |
| `bars_vix.csv.gz` | Cboe VIX daily close (holidays removed) | 2014-01-03 – 2019-01-03 |
| `macro.csv.gz` | Moody's AAA & BAA yields (monthly), core CPI (CPILFESL, monthly) | 1919/1957 – 2018 |

`tests/conftest.py` injects these through the provider seam (`history._fetch`, `fred.observations`) so every analysis path runs
on genuine observations without network access. The data ends in 2018, so every status in tests reads STALE, as it should.
