/** What-if form rules and the shared scale of the result ranges. */
import type { Band, ScenarioIn } from "@/lib/api/types";

/** The backend refuses larger changes (backend/app/ml/whatif/scenarios.py MAX_CHANGE_PCT). */
export const MAX_CHANGE = 30;
export const MAX_SCENARIOS = 3;

/** Clamp a typed change to a whole percent within +/-30; anything unreadable becomes 0. */
export function clampChange(value: number | string): number {
  const n = typeof value === "number" ? value : Number.parseFloat(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(-MAX_CHANGE, Math.min(MAX_CHANGE, Math.round(n)));
}

/** A scenario is ready to send when its scope has a target where one is needed. */
export function isComplete(s: ScenarioIn): boolean {
  return s.scope === "all" || Boolean(s.target);
}

/** One domain for every range so bars are comparable; it always includes zero. */
export function sharedDomain(bands: readonly (Band | null | undefined)[]): [number, number] {
  let lo = 0;
  let hi = 0;
  for (const b of bands) {
    if (!b) continue;
    lo = Math.min(lo, b.low, b.expected, b.high);
    hi = Math.max(hi, b.low, b.expected, b.high);
  }
  if (lo === hi) return [lo - 1, hi + 1];
  return [lo, hi];
}

/** Position of a value on the domain, 0-100 (percent of the track). */
export function position(value: number, [lo, hi]: [number, number]): number {
  return ((value - lo) / (hi - lo)) * 100;
}
