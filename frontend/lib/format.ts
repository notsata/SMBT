export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function pct(v: unknown, digits = 1, signed = false): string {
  if (!isNum(v)) return "—";
  const s = (v * 100).toFixed(digits) + "%";
  return signed && v > 0 ? "+" + s : s;
}

export function num(v: unknown, digits = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function signedClass(v: unknown, invert = false): string {
  if (!isNum(v) || v === 0) return "text-muted";
  return (v > 0) !== invert ? "text-up" : "text-down";
}

export function daysAgo(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
}

export const REGIME_TONE: Record<string, "up" | "down" | "warn" | "muted"> = {
  strong_bull: "up",
  bull_correction: "warn",
  sideways: "muted",
  early_bear: "down",
  bear_capitulation: "down",
  recovery: "up",
  distribution: "warn",
};

export const STATE_TONE: Record<string, "up" | "down" | "warn" | "muted"> = {
  normal_pullback: "up",
  possible_accumulation: "up",
  uptrend_intact: "up",
  structural_decline: "down",
  potential_exhaustion: "warn",
  neutral: "muted",
};

export const HORIZONS = ["5", "10", "20", "60", "126", "252"];
