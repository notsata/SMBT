"""Sentiment and macro feature frames from point-in-time aligned inputs (see data.store.pit_series)."""

from collections.abc import Callable

import numpy as np
import pandas as pd

from .technical import expanding_pct

PitFn = Callable[..., pd.Series]  # pit(series_id, transform=None) -> Series aligned to the market index


def yoy(s: pd.Series) -> pd.Series:
    return s.pct_change(12, fill_method=None) * 100


def sahm(s: pd.Series) -> pd.Series:
    ma3 = s.rolling(3).mean()
    return ma3 - ma3.shift(1).rolling(12, min_periods=12).min()


def sentiment_frame(index: pd.DatetimeIndex, vix: pd.Series | None, vix3m: pd.Series | None, pit: PitFn) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    if vix is not None:
        v = vix.reindex(index)
        out["vix"] = v
        out["vix_vs50"] = v / v.rolling(50, min_periods=40).mean()
    if vix is not None and vix3m is not None:
        out["vix_term"] = vix.reindex(index) / vix3m.reindex(index)  # > 1 = backwardation (stress)
    pcr = pit("PCR_EQUITY")
    out["pcr10"] = pcr.rolling(10, min_periods=8).mean()
    out["aaii_spread"] = pit("AAII_SPREAD")
    out["naaim"] = pit("NAAIM")
    out["short_interest"] = pit("SHORT_INTEREST")
    return out


def macro_frame(index: pd.DatetimeIndex, pit: PitFn) -> pd.DataFrame:
    out = pd.DataFrame(index=index)
    curve = pit("T10Y2Y")
    out["curve_10y2y"] = curve
    out["curve_10y3m"] = pit("T10Y3M")
    inv_days = (curve < 0).astype(float).where(curve.notna()).rolling(252, min_periods=60).sum()
    out["curve_inverted_days"] = inv_days
    out["curve_uninversion"] = (curve > 0) & (inv_days >= 60)
    ff = pit("DFF")
    out["fed_funds"] = ff
    out["fed_chg252"] = ff - ff.shift(252)
    out["real_10y"] = pit("DFII10")
    out["real_chg126"] = out["real_10y"] - out["real_10y"].shift(126)
    credit = pit("BAA10Y")
    if credit.isna().all():  # long-history monthly fallback (Moody's Baa - Aaa)
        credit = pit("BAA") - pit("AAA")
    out["credit_spread"] = credit
    out["credit_chg20"] = credit - credit.shift(20)
    out["credit_chg63"] = credit - credit.shift(63)
    out["hy_oas"] = pit("BAMLH0A0HYM2")
    out["nfci"] = pit("NFCI")
    out["sahm"] = pit("UNRATE", transform=sahm)
    out["unrate"] = pit("UNRATE")
    out["cpi_yoy"] = pit("CPIAUCSL", transform=yoy)
    out["core_cpi_yoy"] = pit("CPILFESL", transform=yoy)
    out["philly_mfg"] = pit("GACDFSA066MSFRBPHI")
    # FRED units: WALCL and WTREGEN in $ millions, RRPONTSYD in $ billions -> net liquidity in $ billions.
    nl = pit("WALCL") / 1000 - pit("WTREGEN") / 1000 - pit("RRPONTSYD").fillna(0)
    out["net_liquidity"] = nl.where(nl > 0)
    out["net_liq_chg63"] = out["net_liquidity"].pct_change(63, fill_method=None)
    out["m2_yoy"] = pit("M2SL", transform=yoy)
    out["mcap_gdp"] = pit("NCBEILQ027S") / 1000 / pit("GDP") * 100
    out["cape"] = pit("CAPE")
    out["rf_rate"] = pit("DTB3")
    return out


def fear_greed(f: pd.DataFrame) -> pd.DataFrame:
    """Transparent reconstruction following CNN's published seven-input design. Not CNN's index.
    Each input -> point-in-time percentile (0 fear .. 100 greed); the index is the mean of available inputs."""
    comps = {}

    def add(name: str, col: str, invert: bool = False):
        if col in f and f[col].notna().sum() > 300:
            p = expanding_pct(f[col], 252)
            comps[name] = (1 - p if invert else p) * 100

    if "close" in f:
        f = f.assign(_mom=f["close"] / f["close"].rolling(125).mean() - 1)
        add("momentum (vs 125d avg)", "_mom")
    add("strength (new highs - lows)", "nhnl_pct")
    add("breadth (McClellan summation)", "mcclellan_sum")
    add("put/call (10d)", "pcr10", invert=True)
    add("volatility (VIX vs 50d)", "vix_vs50", invert=True)
    add("safe haven (stocks - bonds 20d)", "stock_bond_20")
    add("junk bond demand (credit spread)", "credit_spread", invert=True)
    df = pd.DataFrame(comps, index=f.index)
    out = pd.DataFrame(index=f.index)
    out["fear_greed"] = df.mean(axis=1).where(df.notna().sum(axis=1) >= 3)
    out["fear_greed_n"] = df.notna().sum(axis=1)
    return out.join(df.add_prefix("fg:"))


def macro_risk(f: pd.DataFrame) -> pd.DataFrame:
    """Heuristic 0-100 macro risk with named components (each 0..1)."""
    c = pd.DataFrame(index=f.index)
    if f["curve_10y2y"].notna().any():
        c["yield curve inverted / un-inverting"] = np.maximum((f["curve_10y2y"] < 0).astype(float), f["curve_uninversion"].astype(float)).where(f["curve_10y2y"].notna())
    if f["credit_spread"].notna().any():
        c["credit spread percentile"] = expanding_pct(f["credit_spread"], 252)
        c["credit widening (63d)"] = expanding_pct(f["credit_chg63"], 252)
    if f["nfci"].notna().any():
        c["financial conditions tight"] = (f["nfci"].clip(-0.5, 0.5) + 0.5).where(f["nfci"].notna())
    if f["sahm"].notna().any():
        c["unemployment rising (Sahm)"] = (f["sahm"] / 0.5).clip(0, 1)
    if f["fed_chg252"].notna().any():
        c["Fed tightening (12m change)"] = (f["fed_chg252"] / 2.0).clip(0, 1)
    if f["real_10y"].notna().any():
        c["real yields high"] = expanding_pct(f["real_10y"], 252)
    if f["philly_mfg"].notna().any():
        c["manufacturing contraction"] = (-f["philly_mfg"] / 20).clip(0, 1).where(f["philly_mfg"].notna())
    if f["core_cpi_yoy"].notna().any():
        c["core inflation above 3%"] = ((f["core_cpi_yoy"] - 2) / 2).clip(0, 1).where(f["core_cpi_yoy"].notna())
    out = pd.DataFrame(index=f.index)
    out["macro_risk"] = c.mean(axis=1).where(c.notna().sum(axis=1) >= 2) * 100
    return out.join(c.add_prefix("mr:"))
