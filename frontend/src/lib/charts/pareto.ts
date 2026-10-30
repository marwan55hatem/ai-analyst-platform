/** Ranked contributor bars from Pareto insights (one insight per dimension). */
import type { Insight, ParetoItem } from "@/lib/api/types";

export type Dimension = "product" | "category" | "region" | "channel";
export const DIMENSIONS: Dimension[] = ["product", "category", "region"];

export interface BarDatum {
  label: string;
  value: number;
  /** Share of the total in percent, printed next to the bar. */
  share?: number;
  /** Running share in percent (Pareto), printed in the tooltip. */
  cumulative?: number;
  /** Texture that separates groups without colour (ABC class). */
  pattern?: "solid" | "hatch" | "dots";
  group?: string;
}

const PATTERN = { A: "solid", B: "hatch", C: "dots" } as const;

export function paretoBars(items: readonly ParetoItem[], limit = 10): BarDatum[] {
  return items.slice(0, limit).map((i) => ({
    label: i.item,
    value: i.revenue,
    share: i.share_pct,
    cumulative: i.cumulative_pct,
    pattern: PATTERN[i.abc] ?? "solid",
    group: i.abc,
  }));
}

/** Pareto insight of each dimension present in the run (dead-stock insights are ignored). */
export function byDimension(insights: readonly Insight[]): Partial<Record<Dimension, Insight>> {
  const out: Partial<Record<Dimension, Insight>> = {};
  for (const insight of insights) {
    if (insight.type !== "pareto") continue;
    const dim = insight.payload.dimension as Dimension | undefined;
    if (dim && !out[dim]) out[dim] = insight;
  }
  return out;
}

export function itemsOf(insight: Insight | undefined): ParetoItem[] {
  const items = insight?.payload.items;
  return Array.isArray(items) ? (items as ParetoItem[]) : [];
}
