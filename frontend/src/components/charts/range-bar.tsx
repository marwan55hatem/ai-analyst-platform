"use client";

import type { Band } from "@/lib/api/types";
import { position } from "@/lib/charts/whatif";
import { cn } from "@/lib/utils";

/**
 * A low-to-high range on a shared axis with a tick at the expected value and a zero line.
 * The three numbers are printed underneath, so the bar is never read alone.
 */
export function RangeBar({
  band,
  domain,
  format,
  labels,
}: {
  band: Band;
  domain: [number, number];
  format: (n: number) => string;
  labels: { low: string; expected: string; high: string };
}) {
  const lo = position(band.low, domain);
  const hi = position(band.high, domain);
  const mid = position(band.expected, domain);
  const zero = position(0, domain);
  return (
    <div className="space-y-1.5" data-testid="range-bar">
      <div className="relative h-6" dir="ltr" aria-hidden="true">
        <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
        <span className="absolute top-0 h-6 w-px bg-input" style={{ left: `${zero}%` }} />
        <span
          className="absolute top-1.5 h-3 rounded-xs bg-chart-forecast/35 ring-1 ring-chart-forecast"
          style={{ left: `${Math.min(lo, hi)}%`, width: `${Math.max(1, Math.abs(hi - lo))}%` }}
        />
        <span className="absolute top-0.5 h-5 w-0.5 bg-foreground" style={{ left: `calc(${mid}% - 1px)` }} />
      </div>
      <dl className="grid grid-cols-3 gap-2 text-xs">
        {(["low", "expected", "high"] as const).map((k) => (
          <div key={k} className={cn(k === "expected" ? "text-center" : k === "high" ? "text-end" : "text-start")}>
            <dt className="text-muted-foreground">{labels[k]}</dt>
            <dd className={cn("tabular-nums", k === "expected" && "font-semibold")}>{format(band[k])}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Signed bars around a centre line (e.g. reasons that raise or lower a risk), with words. */
export function SignedBars({
  items,
  max,
}: {
  items: { key: string; label: string; value: number; tone: "up" | "down"; word: string }[];
  max: number;
}) {
  return (
    <ul className="space-y-3">
      {items.map((item) => {
        const width = max ? (Math.abs(item.value) / max) * 50 : 0;
        return (
          <li key={item.key} className="space-y-1">
            <p className="text-sm">
              <span className={cn("font-medium", item.tone === "up" ? "text-bad" : "text-good")}>
                {item.tone === "up" ? "▲ " : "▼ "}
                {item.word}
              </span>{" "}
              <span lang="en" dir="ltr">
                {item.label}
              </span>
            </p>
            <div className="relative h-2.5 rounded-xs bg-muted" dir="ltr" aria-hidden="true">
              <span className="absolute inset-y-0 w-px bg-input" style={{ left: "50%" }} />
              <span
                className={cn("absolute inset-y-0 rounded-xs", item.tone === "up" ? "bg-bad" : "pattern-hatch text-good")}
                style={item.tone === "up" ? { left: "50%", width: `${width}%` } : { right: "50%", width: `${width}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
