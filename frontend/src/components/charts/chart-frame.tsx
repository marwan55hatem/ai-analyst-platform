"use client";

import { Table2, LineChart as ChartIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export interface ChartTable {
  columns: string[];
  rows: (string | number)[][];
  /** Columns holding numbers are end-aligned with tabular figures. */
  numeric?: number[];
}

/**
 * The frame every chart sits in: a title, the one-sentence takeaway, the unit, and a
 * "View as table" switch that shows the same numbers as an accessible table.
 */
export function ChartFrame({
  title,
  takeaway,
  unit,
  badges,
  actions,
  table,
  loading = false,
  height = 280,
  children,
  footer,
  className,
  testId,
}: {
  title: ReactNode;
  takeaway?: ReactNode;
  unit?: ReactNode;
  badges?: ReactNode;
  actions?: ReactNode;
  table?: ChartTable;
  loading?: boolean;
  height?: number;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  testId?: string;
}) {
  const t = useTranslations("charts");
  const [asTable, setAsTable] = useState(false);
  const titleId = useId();
  return (
    <figure
      aria-labelledby={titleId}
      className={cn("min-w-0 space-y-3 rounded-lg border bg-card p-4 sm:p-5", className)}
      data-testid={testId}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
          {takeaway ? <p className="max-w-prose text-sm text-muted-foreground">{takeaway}</p> : null}
          {badges ? <div className="flex flex-wrap gap-2 pt-1">{badges}</div> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {actions}
          {table && !loading ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-pressed={asTable}
              onClick={() => setAsTable((v) => !v)}
            >
              {asTable ? <ChartIcon aria-hidden="true" /> : <Table2 aria-hidden="true" />}
              {asTable ? t("viewChart") : t("viewTable")}
            </Button>
          ) : null}
        </div>
      </div>
      {unit ? <p className="text-xs text-muted-foreground">{unit}</p> : null}
      {loading ? (
        <Skeleton className="w-full" style={{ height }} aria-label={t("loading")} />
      ) : asTable && table ? (
        <DataTable table={table} caption={title} />
      ) : (
        children
      )}
      {footer ? <figcaption className="text-sm text-muted-foreground">{footer}</figcaption> : null}
    </figure>
  );
}

/** The chart's numbers as a plain table (also used by "View as table"). */
export function DataTable({ table, caption }: { table: ChartTable; caption?: ReactNode }) {
  const numeric = new Set(table.numeric ?? []);
  return (
    <div className="max-h-96 overflow-auto rounded-md border" tabIndex={0} role="region" aria-label={typeof caption === "string" ? caption : undefined}>
      <table className="w-full text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead className="sticky top-0 bg-muted">
          <tr>
            {table.columns.map((c, i) => (
              <th
                key={c}
                scope="col"
                className={cn("px-3 py-2 font-medium whitespace-nowrap", numeric.has(i) ? "text-end" : "text-start")}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {table.rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => (
                <td
                  key={i}
                  className={cn("px-3 py-1.5", numeric.has(i) ? "text-end tabular-nums" : "text-start")}
                  dir={numeric.has(i) ? undefined : "auto"}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
