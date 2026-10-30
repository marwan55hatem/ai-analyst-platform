/**
 * Merge an actual series, a forecast with its 80/95% ranges and anomaly events onto one list of
 * periods for the line-with-band chart. Values are copied, never computed.
 */
import type { AnomalyEvent, ForecastSpec } from "@/lib/api/types";

export type Marker = "spike" | "drop";

export interface TrendPoint {
  period: string;
  actual: number | null;
  forecast: number | null;
  band80: [number, number] | null;
  band95: [number, number] | null;
  marker: Marker | null;
  /** The anomaly behind the marker, for the tooltip and the list under the chart. */
  event: AnomalyEvent | null;
  partial: boolean;
}

export interface ActualPoint {
  period: string;
  value: number;
  partial?: boolean;
}

/** Return the period (ISO start) an event belongs to: the last period starting on or before it. */
export function periodOf(day: string, periods: readonly string[]): string | null {
  let found: string | null = null;
  for (const period of periods) {
    if (period <= day) found = period;
    else break;
  }
  return found;
}

/**
 * Build chart points. The forecast line starts at the last actual point so the two lines join.
 * Events before the first period or after `rangeEnd` are dropped; when several fall in one
 * period, the largest change is marked.
 */
export function buildTrend(
  actual: readonly ActualPoint[],
  forecast: ForecastSpec["forecast"] = [],
  events: readonly AnomalyEvent[] = [],
  rangeEnd?: string,
): TrendPoint[] {
  const points: TrendPoint[] = actual.map((p) => ({
    period: p.period,
    actual: p.value,
    forecast: null,
    band80: null,
    band95: null,
    marker: null,
    event: null,
    partial: Boolean(p.partial),
  }));
  const periods = points.map((p) => p.period);
  const last = points.at(-1);
  if (forecast.length && last && last.actual !== null) {
    last.forecast = last.actual;
    for (const f of forecast) {
      if (f.period <= last.period) continue;
      points.push({
        period: f.period,
        actual: null,
        forecast: f.point,
        band80: [f.lo80, f.hi80],
        band95: [f.lo95, f.hi95],
        marker: null,
        event: null,
        partial: false,
      });
    }
  }
  for (const event of events) {
    if (rangeEnd && event.start > rangeEnd) continue;
    const period = periodOf(event.start, periods);
    const point = period ? points.find((p) => p.period === period) : undefined;
    if (!point) continue;
    if (!point.event || Math.abs(event.change) > Math.abs(point.event.change)) {
      point.event = event;
      point.marker = event.kind;
    }
  }
  return points;
}

/** Index of the first forecast-only point, or -1 when there is no forecast. */
export function forecastStart(points: readonly TrendPoint[]): number {
  return points.findIndex((p) => p.actual === null && p.forecast !== null);
}
