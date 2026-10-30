"use client";

import { memo, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { GridCell } from "@/lib/charts/rfm";

const LEVEL_STYLE = [0, 1, 2, 3, 4].map((l) => ({
  background: `var(--seq-${l})`,
  color: `var(--seq-${l}-fg)`,
}));

/**
 * A grid of cells on one sequential ramp with the value printed in each cell. It is a real
 * table, so screen readers get row and column headers; the fill only adds a quick read.
 */
export const Heatmap = memo(function Heatmap({
  rows,
  caption,
  rowTitle,
  colTitle,
  rowLabel,
  colLabel,
  cellText,
  cellTooltip,
  legend,
}: {
  rows: GridCell[][];
  caption: string;
  rowTitle: string;
  colTitle: string;
  rowLabel: (row: number) => string;
  colLabel: (col: number) => string;
  cellText: (cell: GridCell) => string;
  cellTooltip?: (cell: GridCell) => ReactNode;
  legend?: { low: string; high: string };
}) {
  const cols = rows[0] ?? [];
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[20rem] border-separate border-spacing-1 text-sm" dir="ltr" data-testid="heatmap">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="w-20 text-start text-xs font-medium text-muted-foreground">
                <span className="sr-only">{rowTitle}</span>
              </th>
              {cols.map((c) => (
                <th key={c.col} scope="col" className="text-center text-xs font-medium text-muted-foreground">
                  {colLabel(c.col)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row[0]?.row}>
                <th scope="row" className="pe-2 text-start text-xs font-medium whitespace-nowrap text-muted-foreground">
                  {rowLabel(row[0]?.row ?? 0)}
                </th>
                {row.map((cell) => {
                  const body = (
                    <td
                      key={cell.col}
                      className="h-12 rounded-sm text-center tabular-nums"
                      style={LEVEL_STYLE[cell.level]}
                      data-level={cell.level}
                    >
                      {cellText(cell)}
                    </td>
                  );
                  return cellTooltip && cell.customers ? (
                    <Tooltip key={cell.col}>
                      <TooltipTrigger asChild>{body}</TooltipTrigger>
                      <TooltipContent>{cellTooltip(cell)}</TooltipContent>
                    </Tooltip>
                  ) : (
                    body
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {rowTitle} ↓ · {colTitle} →
        </span>
        {legend ? (
          <span className="flex items-center gap-2" aria-hidden="true">
            {legend.low}
            <span className="flex">
              {LEVEL_STYLE.slice(1).map((s, i) => (
                <span key={i} className="h-3 w-5 border border-border" style={{ background: s.background }} />
              ))}
            </span>
            {legend.high}
          </span>
        ) : null}
      </div>
    </div>
  );
});
