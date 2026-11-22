"""Alert rules: edge-triggered with hysteresis, per-rule cooldown and a global daily cap.

A rule fires on the transition into its condition, then must re-arm (value moves back past the threshold by
`rearm`) before it can fire again. That plus cooldown keeps a value hovering at a threshold from spamming.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import services as S
from .config import get_settings
from .db import AlertEvent, AlertRule, PushSub, WatchItem, utcnow

log = logging.getLogger(__name__)

KINDS = {
    "score_above": "Score rises above threshold",
    "score_below": "Score falls below threshold",
    "vix_range": "VIX enters a range",
    "breadth_extreme": "Breadth metric reaches an extreme",
    "divergence": "RSI divergence confirmed",
    "regime_change": "Market regime changes",
    "watchlist_setup": "Watchlisted symbol shows a scanner setup",
}
SCORES = {"bottom": "Bottom Opportunity Score", "top": "Top Risk Score", "trend": "Trend Confidence"}
BREADTH = {"pct_above_50": "% above 50-day", "pct_above_200": "% above 200-day", "pct_oversold": "% oversold", "mcclellan_osc": "McClellan Oscillator"}


def validate(kind: str, params: dict) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind}")
    p = dict(params or {})
    num = lambda k, d=None: float(p.get(k, d)) if p.get(k, d) is not None else None  # noqa: E731
    if kind in ("score_above", "score_below"):
        if p.get("score") not in SCORES:
            raise ValueError("score must be bottom|top|trend")
        p["threshold"] = num("threshold")
        if p["threshold"] is None or not 0 <= p["threshold"] <= 100:
            raise ValueError("threshold must be 0-100")
        p["rearm"] = num("rearm", 5)
    elif kind == "vix_range":
        p["min"], p["max"] = num("min", 0), num("max", 200)
        if p["min"] >= p["max"]:
            raise ValueError("min must be < max")
    elif kind == "breadth_extreme":
        if p.get("metric") not in BREADTH or p.get("op") not in ("above", "below"):
            raise ValueError("metric must be one of %s and op above|below" % list(BREADTH))
        p["value"] = num("value")
        p["rearm"] = num("rearm", 5)
    elif kind == "divergence":
        if p.get("direction") not in ("bullish", "bearish"):
            raise ValueError("direction must be bullish|bearish")
    elif kind == "watchlist_setup":
        p["direction"] = p.get("direction", "any")
    if kind != "watchlist_setup":
        p["symbol"] = (p.get("symbol") or "").upper() or None
    return p


def _crossed(value, th, above: bool, armed: bool, rearm: float) -> tuple[bool, bool]:
    """Returns (fire, armed_after)."""
    if value is None:
        return False, armed
    cond = value >= th if above else value <= th
    if cond and armed:
        return True, False
    if not armed and (value < th - rearm if above else value > th + rearm):
        return False, True
    return False, armed


def evaluate_rule(s: Session, rule: AlertRule) -> dict | None:
    p = rule.params
    sym = p.get("symbol") or S.default_benchmark(s)
    state = dict(rule.last_state or {})
    msg = None
    if rule.kind in ("score_above", "score_below"):
        ov = S.overview(s, sym)
        v = ov["cards"][p["score"]]["value"]
        fire, rule.armed = _crossed(v, p["threshold"], rule.kind == "score_above", rule.armed, p["rearm"])
        state["value"] = v
        if fire:
            msg = (f"{sym} {SCORES[p['score']]} {'above' if rule.kind == 'score_above' else 'below'} {p['threshold']:g}",
                   f"Now {v} as of {ov['as_of']} (heuristic score; see validation stats in the app).", {"symbol": sym, "value": v, "as_of": ov["as_of"]})
    elif rule.kind == "vix_range":
        ov = S.overview(s, sym)
        v = ov["cards"]["vix"]["value"]
        inside = v is not None and p["min"] <= v <= p["max"]
        if inside and rule.armed:
            msg = (f"VIX in {p['min']:g}-{p['max']:g}", f"VIX {v} as of {ov['cards']['vix']['as_of']} (end-of-day).", {"value": v})
            rule.armed = False
        elif v is not None and not rule.armed and (v < p["min"] - 0.5 or v > p["max"] + 0.5):
            rule.armed = True
        state["value"] = v
    elif rule.kind == "breadth_extreme":
        f, _, _ = S.market_frame(s, sym)
        v = f[p["metric"]].iloc[-1] if p["metric"] in f else None
        v = None if v is None or v != v else round(float(v), 2)
        fire, rule.armed = _crossed(v, p["value"], p["op"] == "above", rule.armed, p["rearm"])
        state["value"] = v
        if fire:
            msg = (f"Breadth: {BREADTH[p['metric']]} {p['op']} {p['value']:g}", f"{v} as of {f.index[-1].date()}", {"value": v})
    elif rule.kind == "divergence":
        sig = S.signals(s, sym)
        evs = [e for e in sig["events"] if e["type"] == f"{p['direction']}_divergence"]
        latest = evs[-1]["date"] if evs else None
        if latest and state.get("seen") and latest > state["seen"]:
            msg = (f"{sym}: {p['direction']} RSI divergence confirmed", f"Confirmed {latest}.", {"symbol": sym, "date": latest})
        state["seen"] = latest or state.get("seen")
    elif rule.kind == "regime_change":
        ov = S.overview(s, sym)
        reg = ov["cards"]["regime"]["value"]
        if state.get("regime") and reg and reg != state["regime"]:
            msg = (f"{sym} regime: {ov['cards']['regime']['display']}", f"Changed from {state['regime']} as of {ov['as_of']}.", {"symbol": sym, "regime": reg})
        state["regime"] = reg or state.get("regime")
    elif rule.kind == "watchlist_setup":
        from .scanner import FLAGS, scan

        wl = {w.symbol for w in s.execute(select(WatchItem)).scalars()}
        rows = [r for r in scan(s, direction="", limit=10_000)["rows"] if r["symbol"] in wl]
        cur = sorted(f"{r['symbol']}:{fl}" for r in rows for fl in r["flags"] if p["direction"] in ("any", FLAGS[fl][1]))
        new = [x for x in cur if x not in set(state.get("active", []))]
        if new and "active" in state:
            msg = ("Watchlist setup" + ("s" if len(new) > 1 else ""), ", ".join(f"{x.split(':')[0]} {FLAGS[x.split(':')[1]][0]}" for x in new[:5]), {"new": new})
        state["active"] = cur
    rule.last_state = state
    if not msg:
        return None
    return {"title": msg[0], "body": msg[1], "payload": msg[2]}


def evaluate_all(s: Session, now: datetime | None = None) -> list[AlertEvent]:
    now = now or utcnow()
    cap = get_settings().max_alerts_per_day
    sent_today = s.execute(select(func.count()).select_from(AlertEvent).where(AlertEvent.fired_at >= now - timedelta(days=1))).scalar() or 0
    fired = []
    for rule in s.execute(select(AlertRule).where(AlertRule.enabled == True)).scalars():  # noqa: E712
        try:
            res = evaluate_rule(s, rule)
        except S.NotFound as e:
            log.info("alert %s skipped: %s", rule.id, e)
            continue
        if not res:
            continue
        last = rule.last_fired_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last and now - last < timedelta(minutes=rule.cooldown_minutes):
            continue
        if sent_today >= cap:
            log.warning("daily alert cap %s reached; suppressing %s", cap, res["title"])
            continue
        ev = AlertEvent(rule_id=rule.id, title=res["title"], body=res["body"], payload=res["payload"], fired_at=now)
        s.add(ev)
        rule.last_fired_at = now
        sent_today += 1
        fired.append(ev)
    s.commit()
    for ev in fired:
        ev.delivered = send_push(s, {"title": ev.title, "body": ev.body, "tag": f"rule-{ev.rule_id}", "url": "/alerts"})
    s.commit()
    return fired


def send_push(s: Session, payload: dict) -> int:
    st = get_settings()
    if not (st.vapid_private_key and st.vapid_public_key):
        return 0
    from pywebpush import WebPushException, webpush

    ok = 0
    for sub in s.execute(select(PushSub)).scalars().all():
        try:
            webpush({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, json.dumps(payload),
                    vapid_private_key=st.vapid_private_key, vapid_claims={"sub": st.vapid_subject}, ttl=86400)
            ok += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):  # subscription expired or revoked by the browser
                s.execute(delete(PushSub).where(PushSub.endpoint == sub.endpoint))
            else:
                log.warning("push failed (%s): %s", code, e)
    s.commit()
    return ok
