"use client";

import { memo, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { BarDatum } from "@/lib/charts/pareto";
import { cn } from "@/lib/utils";

const FILL: Record<NonNullable<BarDatum["pattern"]>, string> = {
  solid: "bg-current",
  hatch: "pattern-hatch border border-current",
  dots: "pattern-dots border border-current",
};

/**
 * Ranked horizontal bars with the value and share printed at the end of each row, so the bar
 * length is never the only carrier of the number. Bars grow from the start edge (RTL-aware).
 */
export const BarChart = memo(function BarChart({
  data,
  formatValue,
  formatShare,
  tooltip,
  ariaLabel,
  className,
}: {
  data: BarDatum[];
  formatValue: (n: number) => string;
  formatShare?: (n: number) => string;
  tooltip?: (d: BarDatum) => ReactNode;
  ariaLabel: string;
  className?: string;
}) {
  const max = Math.max(0, ...data.map((d) => Math.abs(d.value)));
  return (
    <ol className={cn("space-y-2", className)} aria-label={ariaLabel} data-testid="bar-chart">
      {data.map((d) => {
        const width = max ? Math.max(1, (Math.abs(d.value) / max) * 100) : 0;
        const row = (
          <li
            key={d.label}
            className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-sm sm:grid-cols-[minmax(0,16rem)_1fr_auto]"
          >
            <span className="truncate" dir="auto" title={d.label}>
              {d.label}
            </span>
            <span className="h-4 rounded-xs bg-muted" aria-hidden="true">
              <span
                className={cn("block h-full rounded-e-xs text-chart-actual", FILL[d.pattern ?? "solid"])}
                style={{ width: `${width}%` }}
              />
            </span>
            <span className="text-end whitespace-nowrap tabular-nums">
              {formatValue(d.value)}
              {d.share !== undefined && formatShare ? (
                <span className="ms-2 text-muted-foreground">{formatShare(d.share)}</span>
              ) : null}
            </span>
          </li>
        );
        return tooltip ? (
          <Tooltip key={d.label}>
            <TooltipTrigger asChild>{row}</TooltipTrigger>
            <TooltipContent side="top">{tooltip(d)}</TooltipContent>
          </Tooltip>
        ) : (
          row
        );
      })}
    </ol>
  );
});

/** Swatch for a bar texture, used in legends next to its word. */
export function PatternSwatch({ pattern }: { pattern: NonNullable<BarDatum["pattern"]> }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block h-3 w-5 rounded-xs text-chart-actual", FILL[pattern])}
    />
  );
}
