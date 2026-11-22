"""AI market analyst: Claude answers only from tool calls into this app's own computed data.

Grounding is enforced structurally (the model has no market data except tool results) and by instruction.
Every tool result carries its sources and as-of dates; those are returned to the UI as citations.
"""

import json
import logging

import anthropic
import pandas as pd
from sqlalchemy.orm import Session

from . import services as S
from .backtest import validation as V
from .config import get_settings
from .quant import analogs
from .scanner import scan

log = logging.getLogger(__name__)
MAX_TURNS = 8

SYSTEM = """You are the market analyst inside a quantitative research terminal. You explain market conditions using ONLY data returned by your tools.

Rules:
- Every number you state (price, score, percentile, probability, return, count) must come from a tool result in this conversation. Never estimate, recall, or invent market data. If a tool says a metric is unavailable or stale, say so plainly.
- Keep three kinds of evidence distinct and name which one you are using: heuristic scores (0-100 composites, not probabilities), empirical frequencies (counted from history, with sample sizes and episode counts), and historical analogs (similarity, small samples).
- Always present both the bullish and the bearish evidence, then a balanced conclusion. State sample sizes; call results with fewer than ~8 independent episodes anecdotal. Note when a result failed out-of-sample validation (negative Brier skill, p-value or q-value above 0.05/0.10, walk-forward underperformance).
- Data is end-of-day and may be delayed; mention the as-of date.
- Cite sources inline in brackets, e.g. [overview SPY, as of 2026-11-20] or [event study: bottom_score, 60d].
- This is research, not investment advice. Do not tell the user to buy or sell.
- Be concise and structured: short headline answer, then Bullish evidence / Bearish evidence / What history says / Caveats."""

TOOLS = [
    {"name": "get_market_overview", "description": "Current regime, Bottom/Top/Trend scores with component breakdown, VIX, breadth, sentiment, macro risk, and empirical probabilities for an index/ETF (default SPY).",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Ticker, e.g. SPY or QQQ. Omit for the default benchmark."}}, "additionalProperties": False}},
    {"name": "analyze_symbol", "description": "Single-stock/ETF analysis: state classification (normal pullback, possible accumulation, structural decline, potential exhaustion), indicators with historical percentiles, sector and relative strength, divergences, and forward returns after past occurrences of the same state.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"], "additionalProperties": False}},
    {"name": "get_signal_statistics", "description": "Event study for a named signal on a symbol: forward returns at 5-252 sessions vs random entries (p-values), turning-point precision/recall, and stability across eras. Signals: bottom_score, top_score, rsi_oversold, rsi_overbought, rsi_bull_div, rsi_bear_div, macd_bull_div, macd_bear_div, capitulation_volume, extension, golden_cross, death_cross, vix_spike, vix_backwardation, zweig_thrust, washout, breadth_divergence.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "signal": {"type": "string"}, "threshold": {"type": "number", "description": "Score threshold for bottom_score/top_score (default 70)."}},
                      "required": ["signal"], "additionalProperties": False}},
    {"name": "get_signal_scoreboard", "description": "All signals for a symbol ranked by 60-session evidence with p-values and FDR-adjusted q-values; shows which are active now.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "additionalProperties": False}},
    {"name": "get_correction_frequency", "description": "How often a drawdown of at least `threshold_pct` within `horizon_days` followed past days whose Top Risk Score was in the same 10-point bucket as today, plus the unconditional base rate.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "threshold_pct": {"type": "number"}, "horizon_days": {"type": "integer"}},
                      "required": ["threshold_pct", "horizon_days"], "additionalProperties": False}},
    {"name": "get_historical_analogs", "description": "Closest historical dates to today's conditions (momentum, breadth, sentiment, volatility, valuation, credit, liquidity), named market events, and the distribution of what followed.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "additionalProperties": False}},
    {"name": "run_scanner", "description": "Scan the universe for current bullish or bearish setups ranked by pooled historical evidence. Optional filters: kind (etf|stock), sector, signal id.",
     "input_schema": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["bullish", "bearish"]}, "kind": {"type": "string"}, "sector": {"type": "string"}, "signal": {"type": "string"}},
                      "required": ["direction"], "additionalProperties": False}},
    {"name": "get_data_status", "description": "Which data sources are loaded, their last update dates and staleness, and which metrics are unavailable.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


def _compact_overview(ov: dict) -> dict:
    c = ov["cards"]
    probs = {side: [{k: p.get(k) for k in ("event", "horizon", "probability", "base_rate", "episodes", "sufficient")} | {"oos_brier_skill": (p.get("oos") or {}).get("brier_skill")}
                    for p in ov["probabilities"].get(side, []) if p.get("available")] for side in ("bottom", "top")}
    return {
        "symbol": ov["symbol"], "as_of": ov["as_of"], "data_kind": ov["data_kind"], "price": ov["price"],
        "regime": {"label": c["regime"]["display"], "reasons": c["regime"].get("reasons")},
        "scores": {k: {"value": c[k]["value"], "coverage": c[k].get("coverage")} for k in ("bottom", "top", "trend")},
        "trend_direction": c["trend"].get("direction"),
        "bottom_components": [x for x in ov["explain"]["bottom"]["components"] if x["available"]],
        "top_components": [x for x in ov["explain"]["top"]["components"] if x["available"]],
        "vix": {k: c["vix"].get(k) for k in ("value", "percentile", "term_ratio", "as_of", "source", "stale")},
        "breadth": {k: c["breadth"].get(k) for k in ("value", "pct_above_200", "mcclellan", "nhnl", "note", "as_of")},
        "sentiment": {k: c["sentiment"].get(k) for k in ("value", "components", "source")},
        "macro": {k: c["macro"].get(k) for k in ("value", "components")},
        "probabilities": probs,
        "unavailable_metrics": ov.get("unavailable"),
    }


def run_tool(s: Session, name: str, args: dict) -> tuple[dict, list[dict]]:
    sym = (args.get("symbol") or "").upper() or S.default_benchmark(s)
    if name == "get_market_overview":
        ov = S.overview(s, sym)
        return _compact_overview(ov), [{"tool": name, "symbol": sym, "as_of": ov["as_of"], "source": ov["price"]["source"]}]
    if name == "analyze_symbol":
        a = S.analyze(s, sym)
        return a, [{"tool": name, "symbol": sym, "as_of": a["as_of"], "source": a["source"]}]
    if name in ("get_signal_statistics", "get_signal_scoreboard", "get_correction_frequency", "get_historical_analogs"):
        f, sf, meta = S.market_frame(s, sym)
        b = S.bars(s, sym)
        cite = [{"tool": name, "symbol": sym, "as_of": f.index[-1].date().isoformat(), "source": meta["price"]["source"]}]
        if name == "get_signal_statistics":
            rep = V.signal_report(b, f, sf, args["signal"], float(args.get("threshold") or 70))
            rep["event_study"]["events"] = rep["event_study"]["events"][-15:]
            rep["turning_points"].pop("pivot_dates", None)
            return S.clean(rep), cite
        if name == "get_signal_scoreboard":
            return S.clean({"horizon": 60, "rows": V.signal_scoreboard(b, f, sf)}), cite
        if name == "get_correction_frequency":
            th, h = float(args["threshold_pct"]) / 100, int(args["horizon_days"])
            cur = sf["top"].iloc[-1]
            if pd.isna(cur):
                return {"available": False, "reason": "Top Risk Score unavailable today"}, cite
            lo = (cur // 10) * 10
            cond = (sf["top"] >= lo) & (sf["top"] < lo + 10)
            return S.clean({"top_risk_now": float(cur), "bucket": [lo, lo + 10], "threshold_pct": th * 100, "horizon_days": h,
                            **S.outcome_after_condition(f, cond, h, th)}), cite
        res = analogs.find_analogs(f)
        res["key_dates"] = analogs.compare_key_dates(f)[:6]
        for m in res["matches"]:
            m.pop("features", None)
        return S.clean(res), cite
    if name == "run_scanner":
        r = scan(s, direction=args["direction"], kind=args.get("kind", ""), sector=args.get("sector", ""), signal=args.get("signal", ""), limit=12)
        rows = [{k: x[k] for k in ("symbol", "name", "sector", "flags", f"{args['direction']}_confluence", f"{args['direction']}_evidence", "rsi14", "dist200", "as_of")} for x in r["rows"]]
        return {"rows": rows, "flag_evidence": r["flag_evidence"], "note": r["note"]}, [{"tool": name, "as_of": rows[0]["as_of"] if rows else None, "source": "scanner (EOD)"}]
    if name == "get_data_status":
        st = S.status(s)
        return {k: st[k] for k in ("prices", "breadth", "unavailable", "recent_errors")} | {"series": [x for x in st["series"] if x["available"]]}, [{"tool": name, "source": "data status"}]
    raise ValueError(f"unknown tool {name}")


def ask(s: Session, question: str, history: list[dict] | None = None) -> dict:
    st = get_settings()
    if not st.anthropic_api_key:
        raise RuntimeError("AI analyst disabled: set ANTHROPIC_API_KEY on the backend.")
    client = anthropic.Anthropic(api_key=st.anthropic_api_key, max_retries=3)
    # History is text-only (prior Q&A); tool transcripts are not replayed across questions.
    messages: list = [{"role": m["role"], "content": str(m["content"])[:8000]} for m in (history or [])[-10:] if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({"role": "user", "content": question[:4000]})
    citations, calls = [], []
    resp = None
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=st.analyst_model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return {"answer": "The model declined to answer this request.", "citations": citations, "tool_calls": calls, "model": resp.model}
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for u in uses:
            args = u.input if isinstance(u.input, dict) else {}
            try:
                data, cite = run_tool(s, u.name, args)
                citations += cite
                calls.append({"tool": u.name, "input": args})
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(data, default=str)[:60000]})
            except (S.NotFound, KeyError, ValueError) as e:
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})  # all results in one message keeps parallel tool use working
    text = "\n".join(b.text for b in (resp.content if resp else []) if b.type == "text").strip()
    if resp is not None and resp.stop_reason == "max_tokens":
        text += "\n\n[Answer truncated at the token limit.]"
    return {"answer": text or "No answer produced.", "citations": citations, "tool_calls": calls, "model": resp.model if resp else st.analyst_model}
