/**
 * KPI card logic: which way a change goes, whether that is good news, and how to print it.
 * The numbers themselves come from the backend; nothing here computes a figure.
 */
import type { Kpi, KpiKey, SeriesPoint } from "@/lib/api/types";

export type Direction = "up" | "down" | "flat" | "none";

/** Changes smaller than this (percent or points) read as "about the same". */
export const FLAT_THRESHOLD = 0.05;

/** Every headline KPI on the dashboard is better when it rises. */
const HIGHER_IS_BETTER: Record<KpiKey, boolean> = {
  net_revenue: true,
  orders: true,
  aov: true,
  units: true,
  margin_pct: true,
};

export function direction(kpi: Pick<Kpi, "change">): Direction {
  if (kpi.change === null || kpi.change === undefined || !Number.isFinite(kpi.change)) return "none";
  if (Math.abs(kpi.change) < FLAT_THRESHOLD) return "flat";
  return kpi.change > 0 ? "up" : "down";
}

/** True for good news, false for bad, null when there is no change to judge. */
export function isGood(key: KpiKey, dir: Direction): boolean | null {
  if (dir === "none" || dir === "flat") return null;
  return (dir === "up") === HIGHER_IS_BETTER[key];
}

type SeriesKey = "net_revenue" | "orders" | "aov" | "units" | "margin_pct";

/**
 * Values for a sparkline. Periods without a value (no orders, no cost) are left out, and so are
 * periods only partly inside the range, which would read as a dip.
 */
export function sparkValues(series: readonly SeriesPoint[], key: SeriesKey): number[] {
  const out: number[] = [];
  for (const point of series) {
    if (point.partial) continue;
    const value = point[key];
    if (typeof value === "number" && Number.isFinite(value)) out.push(value);
  }
  return out;
}

export interface Formatters {
  compact: (n: number) => string;
  money: (n: number) => string;
  int: (n: number) => string;
  dec: (n: number) => string;
  signedPct: (n: number) => string;
  signed: (n: number) => string;
}

/** The headline value as text: money and counts shortened when large, margin as a percent. */
export function formatValue(kpi: Pick<Kpi, "unit" | "value">, fmt: Formatters): string {
  const value = kpi.value;
  if (value === null || value === undefined) return "—";
  if (kpi.unit === "percent") return `${fmt.dec(value)}%`;
  if (Math.abs(value) >= 100_000) return fmt.compact(value);
  return kpi.unit === "money" ? fmt.money(value) : fmt.int(value);
}

/** The change as text: "+5.2%" for relative changes, "+1.3" (points) for margin. */
export function formatChange(kpi: Pick<Kpi, "change" | "change_unit">, fmt: Formatters): string | null {
  if (kpi.change === null || kpi.change === undefined) return null;
  return kpi.change_unit === "points" ? fmt.signed(kpi.change) : fmt.signedPct(kpi.change);
}
