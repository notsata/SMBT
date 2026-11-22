"""Every non-price series the app uses: where it comes from and when each value becomes publicly known.

lag_days is applied to the observation date when no true release-date vintage is available (no FRED key,
or a market series that is never revised). It is deliberately conservative: a value is only used on or
after observation_date + lag_days, so backtests cannot see data before it was published.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SeriesDef:
    id: str
    name: str
    category: str  # macro | sentiment | liquidity | valuation | credit
    freq: str
    lag_days: int
    revised: bool  # True -> use ALFRED first-release vintages when FRED_API_KEY is set
    source: str
    note: str = ""


FRED = "FRED (Federal Reserve Bank of St. Louis)"

CATALOG: dict[str, SeriesDef] = {
    s.id: s
    for s in [
        SeriesDef("T10Y2Y", "10Y minus 2Y Treasury spread", "macro", "daily", 1, False, FRED),
        SeriesDef("T10Y3M", "10Y minus 3M Treasury spread", "macro", "daily", 1, False, FRED),
        SeriesDef("DFF", "Effective federal funds rate", "macro", "daily", 1, False, FRED),
        SeriesDef("DGS10", "10Y Treasury yield", "macro", "daily", 1, False, FRED),
        SeriesDef("DFII10", "10Y TIPS real yield", "macro", "daily", 1, False, FRED, "History starts 2003."),
        SeriesDef("DTB3", "3M Treasury bill rate (risk-free)", "macro", "daily", 1, False, FRED),
        SeriesDef("BAA10Y", "Moody's Baa corporate minus 10Y Treasury", "credit", "daily", 1, False, FRED),
        SeriesDef(
            "BAMLH0A0HYM2", "ICE BofA US High Yield OAS", "credit", "daily", 1, False, FRED,
            "FRED may restrict ICE BofA history length under its licence; BAA10Y is the long-history fallback.",
        ),
        SeriesDef("AAA", "Moody's Aaa corporate yield (monthly avg)", "credit", "monthly", 32, False, FRED),
        SeriesDef("BAA", "Moody's Baa corporate yield (monthly avg)", "credit", "monthly", 32, False, FRED),
        SeriesDef("NFCI", "Chicago Fed National Financial Conditions Index", "macro", "weekly", 7, True, FRED),
        SeriesDef("UNRATE", "Unemployment rate", "macro", "monthly", 37, True, FRED),
        SeriesDef("ICSA", "Initial jobless claims", "macro", "weekly", 6, True, FRED),
        SeriesDef("CPIAUCSL", "CPI, all items", "macro", "monthly", 46, True, FRED),
        SeriesDef("CPILFESL", "Core CPI", "macro", "monthly", 46, True, FRED),
        SeriesDef(
            "GACDFSA066MSFRBPHI", "Philadelphia Fed manufacturing: current activity", "macro", "monthly", 21, True, FRED,
            "Free substitute for ISM Manufacturing PMI, which is licensed and no longer on FRED.",
        ),
        SeriesDef("WALCL", "Fed total assets", "liquidity", "weekly", 2, False, FRED),
        SeriesDef("WTREGEN", "Treasury General Account", "liquidity", "weekly", 2, False, FRED),
        SeriesDef("RRPONTSYD", "Overnight reverse repo", "liquidity", "daily", 1, False, FRED),
        SeriesDef("M2SL", "M2 money stock", "liquidity", "monthly", 56, True, FRED),
        SeriesDef(
            "NCBEILQ027S", "Nonfinancial corporate equities, market value", "valuation", "quarterly", 165, True, FRED,
            "Z.1 release ~10 weeks after quarter end; observation date is quarter start.",
        ),
        SeriesDef("GDP", "Nominal GDP", "valuation", "quarterly", 122, True, FRED),
        SeriesDef("PCR_EQUITY", "CBOE equity put/call ratio", "sentiment", "daily", 1, False,
                  "CBOE (archived CSV, 2006-2019) or user import",
                  "CBOE stopped publishing free daily CSV history in 2019; import a licensed file for current data."),
        SeriesDef("PCR_TOTAL", "CBOE total put/call ratio", "sentiment", "daily", 1, False,
                  "CBOE (archived CSV) or user import"),
        SeriesDef("AAII_SPREAD", "AAII bull-bear spread", "sentiment", "weekly", 1, False, "User import (AAII members)",
                  "AAII historical data requires membership; import date,value CSV."),
        SeriesDef("NAAIM", "NAAIM exposure index", "sentiment", "weekly", 1, False, "User import (naaim.org)"),
        SeriesDef("SHORT_INTEREST", "Short interest (% float), benchmark aggregate", "sentiment", "biweekly", 9, False,
                  "User import (FINRA/exchange)"),
        SeriesDef("CAPE", "Shiller CAPE ratio", "valuation", "monthly", 35, False, "User import (Shiller dataset)"),
    ]
}

# Market-wide volatility indexes stored as bars (CBOE official history files).
CBOE_INDEXES = {"^VIX": "VIX", "^VIX3M": "VIX3M", "^VIX9D": "VIX9D", "^VVIX": "VVIX"}

# Metrics that need licensed institutional data; reported as unavailable rather than approximated.
UNAVAILABLE = {
    "gamma_exposure": "Dealer gamma exposure needs full options-chain open interest by strike (licensed OPRA data).",
    "unusual_options": "Unusual options activity needs a licensed real-time options trade feed.",
    "earnings_revisions": "Forward EPS estimates/revisions need a licensed consensus provider (e.g. FactSet, LSEG I/B/E/S).",
    "nyse_advance_decline": "Exchange-wide NYSE A/D data is not free from an official source; breadth uses S&P 500 members instead.",
    "relative_valuation": "Company fundamentals (P/E, EV/EBITDA) need a fundamentals provider; not configured.",
}
