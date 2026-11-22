"""Command line: python -m app.cli {check,report}

check   live verification of every configured provider (REST + streaming); exit code 1 if any configured one fails.
report  signal scoreboard + walk-forward summary for a symbol, computed from live provider history."""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import date, timedelta

log = logging.getLogger("smbt.cli")


async def _probe_stream(cls, symbols: set[str], wait_s: float = 45) -> dict:
    from .market import calendar as cal

    events = {"trades": 0, "quotes": 0, "bars": 0, "index": 0, "latency_ms": []}

    def on_trade(t):
        events["trades"] += 1
        events["latency_ms"].append((t.recv - t.ts) / 1e6)

    s = cls(on_trade=on_trade, on_quote=lambda q: events.__setitem__("quotes", events["quotes"] + 1),
            on_bar=lambda sym, b: events.__setitem__("bars", events["bars"] + 1), on_index=lambda *a: events.__setitem__("index", events["index"] + 1))
    await s.set_symbols(symbols)
    s.start()
    deadline = time.time() + wait_s
    market_open = cal.session_at() != cal.Session.CLOSED
    while time.time() < deadline:
        await asyncio.sleep(1)
        if s.state == "error":
            break
        got = events["trades"] + events["quotes"] + events["index"]
        if s.state == "streaming" and (got >= 5 or (not market_open and s.subscribed)):
            break
    state, error, subscribed = s.state, s.error, bool(s.subscribed)
    await s.stop()
    lat = sorted(events.pop("latency_ms"))
    if state != "streaming" or not subscribed:
        raise RuntimeError(f"{s.name}: {state} {error or ''}".strip())
    return {"state": "authenticated+subscribed", "feed": s.feed, "kind": str(s.kind), **events, "session": str(cal.session_at()),
            "median_event_to_receipt_ms": lat[len(lat) // 2] if lat else None,
            "note": None if market_open else "market closed: no events expected"}


def check() -> int:
    from .config import get_settings
    from .market import calendar as cal
    from .market.providers import alpaca, eod, fmp, fred, massive
    from .market.stream import AlpacaStream, MassiveIndexStream, MassiveStream

    st = get_settings()
    rows: list[tuple[str, str, str]] = []

    def run(name: str, fn, required: bool = True):
        t0 = time.time()
        try:
            detail = fn()
            rows.append((name, "ok", f"{json.dumps(detail, default=str)} ({time.time() - t0:.1f}s)"))
        except Exception as e:
            rows.append((name, "FAIL" if required else "warn", f"{type(e).__name__}: {e}"))

    def frame_info(df):
        return {"rows": len(df), "first": df.index[0].date(), "last": df.index[-1].date(), "last_close": round(float(df["close"].iloc[-1]), 2)}

    recent = date.today() - timedelta(days=10)
    run("cboe: VIX official history", lambda: frame_info(eod.cboe_daily("^VIX")))
    run("cboe: VIX3M official history", lambda: frame_info(eod.cboe_daily("^VIX3M")))
    run("fred: T10Y2Y" + (" (API)" if st.fred_api_key else " (keyless download)"),
        lambda: (lambda d: {"rows": len(d[0]), "last_obs": d[0].index[-1], "source": d[1]})(fred.observations("T10Y2Y", False)))
    if st.fred_api_key:
        run("fred: CPIAUCSL first-release vintages",
            lambda: (lambda d: {"rows": len(d[0]), "last_obs": d[0].index[-1], "published": d[0]["available_at"].iloc[-1]})(fred.observations("CPIAUCSL", True)))
        run("fred: BAA10Y copyright check", lambda: fred.metadata("BAA10Y"))
    if st.tiingo_api_key:
        run("tiingo: SPY daily (dividend-adjusted)", lambda: frame_info(eod.tiingo_daily("SPY", date(1993, 1, 1))))
    if st.has_alpaca:
        run("alpaca: SPY daily bars (SIP history)", lambda: frame_info(alpaca.daily_bars(["SPY"], recent)["SPY"]))
        run(f"alpaca: snapshots ({st.alpaca_feed})",
            lambda: {k: v for k, v in alpaca.snapshots(["SPY", "QQQ"])["SPY"].items() if k in ("last", "last_ts", "bid", "ask", "prev_close")})
        run(f"alpaca: stream {st.alpaca_feed}", lambda: asyncio.run(_probe_stream(AlpacaStream, {"SPY", "QQQ"})))
    if st.massive_api_key:
        run("massive: SPY daily aggregates", lambda: frame_info(massive.daily("SPY", date(2000, 1, 1))))
        run("massive: snapshot", lambda: {k: v for k, v in massive.snapshots(["SPY"])["SPY"].items() if k in ("last", "last_ts", "bid", "ask", "prev_close")})
        run(f"massive: stream ({'real-time' if st.massive_realtime else 'delayed'})", lambda: asyncio.run(_probe_stream(MassiveStream, {"SPY", "QQQ"})))
        if st.massive_indices:
            run("massive: VIX index value", lambda: massive.index_values(["^VIX"]))
            run("massive: indices stream", lambda: asyncio.run(_probe_stream(MassiveIndexStream, {"^VIX"})))
        if st.massive_options:
            run("massive: SPY option chain snapshot",
                lambda: (lambda c: {"contracts_first_page": len(c), "has_greeks": bool(c and c[0].get("greeks"))})(massive.option_chain("SPY")[:250]))
    if st.fmp_api_key:
        run("fmp: AAPL profile", lambda: fmp.profile("AAPL"))
        run("fmp: S&P 500 constituents", lambda: {"members": len(fmp.sp500_constituents())})
        run("fmp: S&P 500 historical changes", lambda: {"changes": len(fmp.sp500_changes())}, required=False)
    print(f"session: {json.dumps(cal.info())}")
    width = max(len(r[0]) for r in rows)
    for name, res, detail in rows:
        print(f"{res:4s}  {name:{width}s}  {detail[:400]}")
    unconfigured = [n for n, ok in (("alpaca", st.has_alpaca), ("massive", bool(st.massive_api_key)), ("tiingo", bool(st.tiingo_api_key)),
                                    ("fmp", bool(st.fmp_api_key)), ("fred api key", bool(st.fred_api_key))) if not ok]
    if unconfigured:
        print("not configured (skipped): " + ", ".join(unconfigured))
    return 1 if any(r[1] == "FAIL" for r in rows) else 0


def report(symbol: str | None) -> int:
    from . import services as S
    from .backtest import validation as V
    from .backtest.strategies import STRATEGIES
    from .market.macro import macro

    macro.refresh_all()
    sym = (symbol or S.default_benchmark()).upper()
    f, sf, meta = S.market_frame(sym)
    b = S.bars(sym)
    print(f"== {sym} {b.index[0].date()}..{b.index[-1].date()} via {b.attrs['provider']} ({b.attrs['adjustment']}); sources {meta['sources']} ==")
    fmt = lambda v, p=3: "  -  " if v is None else f"{v:.{p}f}"  # noqa: E731
    for r in V.signal_scoreboard(b, f, sf):
        print(f"{r['signal']:22s} n={r['n_events']:3d} median={fmt(r['median'])} hit={fmt(r['hit_rate'], 2)} base={fmt(r['baseline_hit_rate'], 2)} "
              f"excess={fmt(r['excess_mean'])} p={fmt(r['p_value'])} q={fmt(r['q_value'])}")
    rf = f["rf_rate"] if f["rf_rate"].notna().any() else None
    print("== walk-forward (6y train / 2y test, 1bp + 2bp costs) ==")
    for sid in STRATEGIES:
        if sid == "buy_hold":
            continue
        r = V.walk_forward(b, f, sf, sid, rf=rf)
        if "error" in r:
            print(f"{sid:14s} {r['error']}")
            continue
        o, bm, d = r["oos"], r["oos_benchmark"], r["oos_vs_benchmark"]
        print(f"{sid:14s} OOS {r['oos_period'][0]}..{r['oos_period'][1]} CAGR {o['cagr']:+.3f} (B&H {bm['cagr']:+.3f}) Sharpe {o['sharpe'] or 0:.2f} "
              f"(B&H {bm['sharpe'] or 0:.2f}) MaxDD {o['max_drawdown']:.2f} (B&H {bm['max_drawdown']:.2f}) dSharpe 95% CI "
              f"[{d['sharpe_diff_ci95'][0]:+.2f}, {d['sharpe_diff_ci95'][1]:+.2f}]")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify every configured provider live")
    rep = sub.add_parser("report", help="scoreboard + walk-forward for a symbol from live history")
    rep.add_argument("symbol", nargs="?")
    a = p.parse_args(argv)
    return check() if a.cmd == "check" else report(a.symbol)


if __name__ == "__main__":
    sys.exit(main())
