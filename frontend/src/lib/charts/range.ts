/**
 * Dashboard date ranges. Presets count back from the last day in the data (an upload is a
 * snapshot, so "last 30 days" means the 30 days before the data ends, not before today).
 */
import type { Grain } from "@/lib/api/types";

export const PRESETS = ["30d", "90d", "12m", "ytd", "all"] as const;
export type Preset = (typeof PRESETS)[number];

/** The backend refuses more points than this (TIMESERIES_MAX_POINTS). */
export const MAX_POINTS = 1500;

export interface DateRange {
  start: string;
  end: string;
}

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function daysIn({ start, end }: DateRange): number {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000) + 1;
}

/** The range of a preset, trimmed to the data. */
export function presetRange(preset: Preset, data: DateRange): DateRange {
  const end = data.end;
  let start: string;
  if (preset === "30d") start = shift(end, -29);
  else if (preset === "90d") start = shift(end, -89);
  else if (preset === "12m") start = shift(end, -364);
  else if (preset === "ytd") start = `${end.slice(0, 4)}-01-01`;
  else start = data.start;
  return { start: start < data.start ? data.start : start, end };
}

/** Days for short ranges, weeks up to about two years, months beyond. */
export function autoGrain(range: DateRange): Grain {
  const days = daysIn(range);
  if (days <= 120) return "day";
  if (days <= 800) return "week";
  return "month";
}

/** Whether a grain keeps the series under the backend's point limit. */
export function grainAllowed(range: DateRange, grain: Grain): boolean {
  const days = daysIn(range);
  const points = grain === "day" ? days : grain === "week" ? days / 7 + 1 : days / 28 + 1;
  return points <= MAX_POINTS;
}

/** A valid ISO date inside the data, or null. */
export function validDay(value: string | null | undefined, data: DateRange): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) return null;
  if (value < data.start) return data.start;
  if (value > data.end) return data.end;
  return value;
}

/** Severity of an insight from its 0-100 impact score. */
export function severity(impact: number): "high" | "medium" | "low" {
  if (impact >= 50) return "high";
  if (impact >= 20) return "medium";
  return "low";
}
