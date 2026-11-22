"""Command line: python -m app.cli {init,sample,ingest,alerts,worker,vapid,report}"""

import argparse
import base64
import json
import logging
import sys

from .db import init_db, session

log = logging.getLogger("smbt.cli")


def vapid_keys() -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    k = ec.generate_private_key(ec.SECP256R1())
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    der = k.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return {"VAPID_PUBLIC_KEY": b64(pub), "VAPID_PRIVATE_KEY": b64(der)}


def worker() -> None:
    """Daily EOD pipeline: ingest -> recompute -> alerts. Runs after the US close and data vendors' EOD update."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    from .alerts import evaluate_all
    from .data.ingest import run_ingest

    def job():
        s = session()
        try:
            res = run_ingest(s)
            log.info("ingest done: %d jobs, %d errors", len(res), sum(r["status"] == "error" for r in res))
            fired = evaluate_all(s)
            log.info("alerts fired: %d", len(fired))
        except Exception:
            log.exception("daily job failed")
            s.rollback()
        finally:
            s.close()

    sched = BlockingScheduler(timezone="America/New_York")
    sched.add_job(job, "cron", day_of_week="mon-fri", hour=18, minute=15, id="eod", max_instances=1, coalesce=True, misfire_grace_time=3600)
    sched.add_job(job, "cron", day_of_week="sat", hour=9, minute=0, id="weekly-catchup", max_instances=1, coalesce=True)
    s = session()
    from .db import kv_get

    if not (kv_get(s, "data_version") or {}).get("v"):
        log.info("empty database: running initial ingest now")
        s.close()
        job()
    else:
        s.close()
    log.info("scheduler started (EOD job 18:15 America/New_York, Mon-Fri)")
    sched.start()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("sample")
    ing = sub.add_parser("ingest")
    ing.add_argument("--no-prices", action="store_true")
    ing.add_argument("--no-series", action="store_true")
    sub.add_parser("alerts")
    sub.add_parser("worker")
    sub.add_parser("vapid")
    rep = sub.add_parser("report", help="print signal scoreboard + walk-forward summary for a symbol")
    rep.add_argument("symbol", nargs="?")
    a = p.parse_args(argv)
    if a.cmd == "vapid":
        print("\n".join(f"{k}={v}" for k, v in vapid_keys().items()))
        return 0
    init_db()
    s = session()
    try:
        if a.cmd == "sample":
            from .data.ingest import load_sample_data

            print(json.dumps(load_sample_data(s)))
        elif a.cmd == "ingest":
            from .data.ingest import run_ingest

            res = run_ingest(s, prices=not a.no_prices, series=not a.no_series)
            errs = [r for r in res if r["status"] == "error"]
            print(f"{len(res)} jobs, {len(errs)} errors")
            for r in errs[:40]:
                print(f"  {r['source']}:{r['target']}: {r['message']}")
        elif a.cmd == "alerts":
            from .alerts import evaluate_all

            print([e.title for e in evaluate_all(s)])
        elif a.cmd == "worker":
            s.close()
            worker()
        elif a.cmd == "report":
            from . import services as S
            from .backtest import validation as V
            from .backtest.strategies import STRATEGIES

            sym = (a.symbol or S.default_benchmark(s)).upper()
            f, sf, _ = S.market_frame(s, sym)
            b = S.bars(s, sym)
            print(f"== {sym} {b.index[0].date()}..{b.index[-1].date()} signal scoreboard (60d, next-open entry) ==")
            for r in V.signal_scoreboard(b, f, sf):
                fmt = lambda v, p=3: "  -  " if v is None else f"{v:.{p}f}"  # noqa: E731
                print(f"{r['signal']:22s} n={r['n_events']:3d} median={fmt(r['median'])} hit={fmt(r['hit_rate'], 2)} base={fmt(r['baseline_hit_rate'], 2)} "
                      f"excess={fmt(r['excess_mean'])} p={fmt(r['p_value'])} q={fmt(r['q_value'])}")
            rf = f["rf_rate"] if f["rf_rate"].notna().any() else None
            print(f"== walk-forward (6y train / 2y test, 1bp + 2bp costs) ==")
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
        s.commit()
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
