"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, ErrorBox, Note, Panel } from "@/components/ui";
import { ApiError, api, type AnalystAnswer } from "@/lib/api";

type Msg = { role: "user" | "assistant"; content: string; meta?: AnalystAnswer };

const SUGGESTIONS = [
  "How close is QQQ to a potential bottom?",
  "Is NVDA experiencing a normal dip or a major trend reversal?",
  "Which indicators suggest the market is overextended?",
  "How often did these conditions precede a 10% correction?",
  "Which ETFs currently have the strongest historically validated bottom signals?",
];

// Minimal, safe rendering: paragraphs, bullet lines and **bold**; no HTML injection.
function Rich({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {text.split(/\n{2,}/).map((block, i) => {
        const lines = block.split("\n");
        const bold = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((part, j) => (part.startsWith("**") && part.endsWith("**") ? <b key={j}>{part.slice(2, -2)}</b> : <span key={j}>{part}</span>));
        if (lines.every((l) => /^\s*[-*•]\s/.test(l)))
          return <ul key={i} className="list-disc space-y-0.5 pl-5">{lines.map((l, j) => <li key={j}>{bold(l.replace(/^\s*[-*•]\s/, ""))}</li>)}</ul>;
        return <p key={i}>{lines.map((l, j) => <span key={j}>{bold(l.replace(/^#+\s*/, ""))}{j < lines.length - 1 && <br />}</span>)}</p>;
      })}
    </div>
  );
}

export default function AnalystPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, busy]);

  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }]);
    setQ("");
    setBusy(true);
    setErr(null);
    try {
      const r = await api<AnalystAnswer>("/analyst", { method: "POST", json: { question, history } });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, meta: r }]);
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-lg font-semibold">AI market analyst</h1>
      <Note>Answers are generated only from this app&apos;s computed indicators, scores, event studies and analogs (retrieved via tools and cited below each answer). Research, not investment advice.</Note>
      {msgs.length === 0 && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => ask(s)} className="rounded-full border border-line px-3 py-1.5 text-left text-xs text-muted hover:border-accent hover:text-fg">{s}</button>
          ))}
        </div>
      )}
      <div className="space-y-3">
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3 py-2 text-sm text-white">{m.content}</div>
          ) : (
            <Panel key={i}>
              <Rich text={m.content} />
              {m.meta && m.meta.citations.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-2">
                  <span className="text-[11px] text-muted">Data used:</span>
                  {m.meta.citations.map((c, j) => (
                    <Badge key={j} tone="muted" title={c.source}>{c.tool.replaceAll("_", " ")}{c.symbol ? ` · ${c.symbol}` : ""}{c.as_of ? ` · ${c.as_of}` : ""}</Badge>
                  ))}
                </div>
              )}
            </Panel>
          ),
        )}
        {busy && <div className="text-sm text-muted">Retrieving data and analyzing…</div>}
        {err && <ErrorBox error={err instanceof ApiError && err.status === 503 ? new Error(err.message) : err} />}
        <div ref={end} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="sticky bottom-20 flex gap-2 lg:bottom-4">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about the market, a ticker, or a signal…" aria-label="Question" className="flex-1 rounded-xl border border-line bg-panel px-3 py-2.5 text-sm shadow-sm" />
        <button disabled={busy} className="rounded-xl bg-accent px-4 text-sm font-medium text-white disabled:opacity-50">Ask</button>
      </form>
    </div>
  );
}
