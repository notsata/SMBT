"use client";

import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import type { ChartData, SignalEvent } from "@/lib/api";

export type Palette = { bg: string; fg: string; muted: string; line: string; up: string; down: string; accent: string; warn: string };

function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return { bg: v("--c-panel"), fg: v("--c-fg"), muted: v("--c-muted"), line: v("--c-line"), up: v("--c-up"), down: v("--c-down"), accent: v("--c-accent"), warn: v("--c-warn") };
}

export function usePalette(): Palette | null {
  const [p, setP] = useState<Palette | null>(null);
  useEffect(() => {
    const update = () => setP(readPalette());
    update();
    window.addEventListener("smbt-theme", update);
    return () => window.removeEventListener("smbt-theme", update);
  }, []);
  return p;
}

function baseOptions(p: Palette, height: number) {
  return {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: p.bg }, textColor: p.muted, fontSize: 11, panes: { separatorColor: p.line } },
    grid: { vertLines: { color: p.line + "55" }, horzLines: { color: p.line + "55" } },
    rightPriceScale: { borderColor: p.line },
    timeScale: { borderColor: p.line, rightOffset: 4 },
    crosshair: { mode: CrosshairMode.Normal },
    handleScale: { pinch: true, mouseWheel: true, axisPressedMouseMove: true },
    handleScroll: { horzTouchDrag: true, vertTouchDrag: false, mouseWheel: true, pressedMouseMove: true },
  };
}

const MARKER: Record<string, { position: "aboveBar" | "belowBar"; shape: "arrowUp" | "arrowDown" | "circle" | "square"; tone: keyof Palette; text: string }> = {
  bottom: { position: "belowBar", shape: "arrowUp", tone: "up", text: "B" },
  top: { position: "aboveBar", shape: "arrowDown", tone: "down", text: "T" },
  bullish_divergence: { position: "belowBar", shape: "circle", tone: "accent", text: "" },
  bearish_divergence: { position: "aboveBar", shape: "circle", tone: "warn", text: "" },
  zweig_thrust: { position: "belowBar", shape: "square", tone: "up", text: "Z" },
  regime_bear_capitulation: { position: "belowBar", shape: "square", tone: "down", text: "C" },
  regime_distribution: { position: "aboveBar", shape: "square", tone: "warn", text: "D" },
};

export const MARKER_TYPES = Object.keys(MARKER);

export function PriceChart({
  data,
  events = [],
  visibleTypes,
  onSelect,
  overlays = ["sma50", "sma200"],
  height = 420,
}: {
  data: ChartData;
  events?: SignalEvent[];
  visibleTypes?: string[];
  onSelect?: (e: SignalEvent | null) => void;
  overlays?: string[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const p = usePalette();
  const [expanded, setExpanded] = useState(false);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!ref.current || !p || !data.bars.length) return;
    const chart = createChart(ref.current, baseOptions(p, expanded ? window.innerHeight - 56 : height));
    chartRef.current = chart;
    const hasOhlc = data.bars.filter((b) => b.o !== null && b.h !== null && b.l !== null).length > data.bars.length * 0.9;
    const main = hasOhlc
      ? chart.addSeries(CandlestickSeries, { upColor: p.up, downColor: p.down, borderVisible: false, wickUpColor: p.up, wickDownColor: p.down })
      : chart.addSeries(LineSeries, { color: p.fg, lineWidth: 2 });
    if (hasOhlc) {
      main.setData(data.bars.map((b) => ({ time: b.t as Time, open: b.o ?? b.c, high: b.h ?? b.c, low: b.l ?? b.c, close: b.c })) as never);
    } else {
      main.setData(data.bars.map((b) => ({ time: b.t as Time, value: b.c })) as never);
    }
    if (data.bars.some((b) => b.v)) {
      const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, color: p.muted + "55" });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(data.bars.map((b, i) => ({ time: b.t as Time, value: b.v ?? 0, color: i && b.c < data.bars[i - 1].c ? p.down + "55" : p.up + "55" })));
    }
    const colors: Record<string, string> = { sma20: p.warn, sma50: p.accent, sma200: p.fg, bb_upper: p.muted, bb_lower: p.muted, avwap_from_252d_low: p.up };
    for (const k of overlays) {
      const vals = data.overlays?.[k];
      if (!vals) continue;
      const s = chart.addSeries(LineSeries, { color: colors[k] ?? p.muted, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(data.bars.flatMap((b, i) => (vals[i] === null || vals[i] === undefined ? [] : [{ time: b.t as Time, value: vals[i] as number }])));
    }
    const shown = events.filter((e) => MARKER[e.type] && (!visibleTypes || visibleTypes.includes(e.type)));
    const markers: SeriesMarker<Time>[] = shown.map((e, i) => {
      const m = MARKER[e.type];
      return { time: e.date as Time, position: m.position, shape: m.shape, color: p[m.tone], text: m.text, id: String(i) };
    });
    createSeriesMarkers(main, markers);
    chart.subscribeClick((param) => {
      if (!onSelectRef.current) return;
      const id = param.hoveredObjectId;
      if (id !== undefined && shown[Number(id)]) return onSelectRef.current(shown[Number(id)]);
      if (typeof param.time === "string") {
        const t = new Date(param.time).getTime();
        const near = shown.filter((e) => Math.abs(new Date(e.date).getTime() - t) <= 3 * 86_400_000);
        onSelectRef.current(near[0] ?? null);
      }
    });
    const n = data.bars.length;
    if (n > 400) chart.timeScale().setVisibleLogicalRange({ from: n - 400, to: n + 3 });
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data, events, visibleTypes, p, overlays, height, expanded]);

  useEffect(() => {
    if (!expanded) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [expanded]);

  useEffect(() => {
    const toggle = () => setExpanded((x) => !x);
    window.addEventListener("smbt-fullscreen", toggle);
    return () => window.removeEventListener("smbt-fullscreen", toggle);
  }, []);

  return (
    <div className={expanded ? "fixed inset-0 z-50 bg-panel p-2 pt-[max(env(safe-area-inset-top),0.5rem)]" : "relative"}>
      <div className="mb-1 flex items-center justify-end gap-2">
        <button onClick={() => chartRef.current?.timeScale().fitContent()} className="rounded border border-line px-2 py-0.5 text-[11px] text-muted hover:text-fg">
          All
        </button>
        <button onClick={() => setExpanded(!expanded)} className="rounded border border-line px-2 py-0.5 text-[11px] text-muted hover:text-fg" aria-label="Toggle full screen chart (f)">
          {expanded ? "Close" : "Full screen"}
        </button>
      </div>
      <div ref={ref} style={{ height: expanded ? "calc(100dvh - 56px)" : height }} className="w-full touch-none" />
    </div>
  );
}

export type LineSpec = { name: string; values: (number | null)[]; color?: string; pane?: number };

export function LineChart({ t, lines, height = 260, priceFormat }: { t: string[]; lines: LineSpec[]; height?: number; priceFormat?: "percent" | "price" }) {
  const ref = useRef<HTMLDivElement>(null);
  const p = usePalette();
  useEffect(() => {
    if (!ref.current || !p || !t.length) return;
    const chart = createChart(ref.current, baseOptions(p, height));
    const auto = [p.accent, p.fg, p.warn, p.up, p.down, p.muted];
    lines.forEach((l, i) => {
      const s = chart.addSeries(
        LineSeries,
        { color: l.color ?? auto[i % auto.length], lineWidth: 2, title: l.name, priceLineVisible: false, ...(priceFormat === "percent" ? { priceFormat: { type: "percent" as const } } : {}) },
        l.pane ?? 0,
      );
      s.setData(t.flatMap((d, j) => (l.values[j] === null || l.values[j] === undefined ? [] : [{ time: d as Time, value: l.values[j] as number }])));
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [t, lines, p, height, priceFormat]);
  return <div ref={ref} style={{ height }} className="w-full touch-none" />;
}
