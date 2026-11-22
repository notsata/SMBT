"""Session alerts. Rules live only in memory for one browser connection (nothing is stored; a refresh clears them).
Each rule is edge-triggered with hysteresis, a per-rule cooldown and a per-session daily cap, so a value hovering
at a threshold cannot spam. Fired alerts are delivered over that connection's WebSocket."""

import time
from datetime import datetime, timezone

from . import services as S
from .market.model import Unavailable

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
MAX_RULES = 20


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
        p["value"], p["rearm"] = num("value"), num("rearm", 5)
    elif kind == "divergence":
        if p.get("direction") not in ("bullish", "bearish"):
            raise ValueError("direction must be bullish|bearish")
    elif kind == "watchlist_setup":
        p["direction"] = p.get("direction", "any")
        p["symbols"] = [str(x).upper()[:16] for x in (p.get("symbols") or [])][:30]
    if kind != "watchlist_setup":
        p["symbol"] = (str(p.get("symbol") or "").upper()[:16]) or None
    return p


def crossed(value, th, above: bool, armed: bool, rearm: float) -> tuple[bool, bool]:
    """(fire, armed_after) for a threshold with hysteresis."""
    if value is None:
        return False, armed
    if (value >= th if above else value <= th) and armed:
        return True, False
    if not armed and (value < th - rearm if above else value > th + rearm):
        return False, True
    return False, armed


class SessionAlerts:
    def __init__(self, max_per_day: int = 20):
        self.rules: list[dict] = []
        self.max_per_day = max_per_day
        self.sent: list[float] = []

    def set_rules(self, rules: list[dict]) -> list[dict]:
        out = []
        for i, r in enumerate(rules[:MAX_RULES]):
            kind = str(r.get("kind"))
            out.append({"id": str(r.get("id") or i), "name": str(r.get("name") or KINDS.get(kind, kind))[:80], "kind": kind,
                        "params": validate(kind, r.get("params") or {}), "cooldown_s": max(60, min(int(r.get("cooldown_minutes", 1440)) * 60, 30 * 86400)),
                        "armed": True, "state": {}, "last_fired": 0.0})
        self.rules = out
        return [{k: r[k] for k in ("id", "name", "kind", "params")} for r in out]

    def evaluate(self) -> list[dict]:
        fired = []
        now = time.time()
        self.sent = [t for t in self.sent if now - t < 86400]
        for r in self.rules:
            try:
                res = evaluate_rule(r)
            except (Unavailable, KeyError, ValueError) as e:
                r["state"]["error"] = str(e)[:200]
                continue
            if not res or now - r["last_fired"] < r["cooldown_s"] or len(self.sent) >= self.max_per_day:
                continue
            r["last_fired"] = now
            self.sent.append(now)
            fired.append({"rule_id": r["id"], "fired_at": datetime.now(timezone.utc).isoformat(), **res})
        return fired


def evaluate_rule(r: dict) -> dict | None:
    p, kind, state = r["params"], r["kind"], r["state"]
    sym = p.get("symbol") or S.default_benchmark()
    if kind in ("score_above", "score_below"):
        card = S.overview(sym)["cards"][p["score"]]
        v = card["value"]
        fire, r["armed"] = crossed(v, p["threshold"], kind == "score_above", r["armed"], p["rearm"])
        state["value"] = v
        if fire:
            return {"title": f"{sym} {SCORES[p['score']]} {'above' if kind == 'score_above' else 'below'} {p['threshold']:g}",
                    "body": f"Now {v} ({card['basis']}, {card['status']}, as of {card['as_of']}). Heuristic score; see Validate for measured performance."}
    elif kind == "vix_range":
        card = S.overview(sym)["cards"]["vix"]
        v = card["value"]
        inside = v is not None and p["min"] <= v <= p["max"]
        state["value"] = v
        if inside and r["armed"]:
            r["armed"] = False
            return {"title": f"VIX in {p['min']:g}-{p['max']:g}", "body": f"VIX {v} ({card['status']}, as of {card['as_of']})."}
        if v is not None and not r["armed"] and (v < p["min"] - 0.5 or v > p["max"] + 0.5):
            r["armed"] = True
    elif kind == "breadth_extreme":
        f, _, _ = S.market_frame(sym)
        raw = f[p["metric"]].iloc[-1] if p["metric"] in f else None
        v = None if raw is None or raw != raw else round(float(raw), 2)
        fire, r["armed"] = crossed(v, p["value"], p["op"] == "above", r["armed"], p["rearm"])
        state["value"] = v
        if fire:
            return {"title": f"Breadth: {BREADTH[p['metric']]} {p['op']} {p['value']:g}", "body": f"{v} as of {f.index[-1].date()} close"}
    elif kind == "divergence":
        evs = [e for e in S.signals(sym)["events"] if e["type"] == f"{p['direction']}_divergence"]
        latest = evs[-1]["date"] if evs else None
        if latest and state.get("seen") and latest > state["seen"]:
            msg = {"title": f"{sym}: {p['direction']} RSI divergence confirmed", "body": f"Confirmed {latest}."}
            state["seen"] = latest
            return msg
        state["seen"] = latest or state.get("seen")
    elif kind == "regime_change":
        card = S.overview(sym)["cards"]["regime"]
        reg = card["value"]
        prev = state.get("regime")
        state["regime"] = reg or prev
        if prev and reg and reg != prev:
            return {"title": f"{sym} regime: {card['display']}", "body": f"Changed from {prev} ({card['basis']}, as of {card['as_of']})."}
    elif kind == "watchlist_setup":
        from .scanner import FLAGS, scan

        wl = set(p["symbols"])
        rows = [x for x in scan(direction="", limit=10_000)["rows"] if x["symbol"] in wl]
        cur = sorted(f"{x['symbol']}:{fl}" for x in rows for fl in x["flags"] if p["direction"] in ("any", FLAGS[fl][1]))
        first = "active" not in state
        new = [x for x in cur if x not in set(state.get("active", []))]
        state["active"] = cur
        if new and not first:
            return {"title": "Watchlist setup" + ("s" if len(new) > 1 else ""),
                    "body": ", ".join(f"{x.split(':')[0]} {FLAGS[x.split(':')[1]][0]}" for x in new[:5])}
    return None
