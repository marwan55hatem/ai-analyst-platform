"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef } from "react";

import { Button } from "@/components/ui/button";
import type { SortOrder } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface ColumnMeta {
  /** Server sort key; columns without one cannot be sorted. */
  sortKey?: string;
  numeric?: boolean;
}

const ROW_HEIGHT = 44;

/**
 * A table whose sorting and paging happen on the server (the API returns one page and the
 * total). Rows are virtualised, so a 500-row page stays light; headers stay real <th>s.
 */
export function ServerTable<T>({
  columns,
  data,
  total,
  offset,
  limit,
  sort,
  order,
  caption,
  loading,
  onSort,
  onPage,
  onRowClick,
  rowLabel,
  getRowId,
}: {
  columns: ColumnDef<T>[];
  data: T[];
  total: number;
  offset: number;
  limit: number;
  sort: string;
  order: SortOrder;
  caption: string;
  loading?: boolean;
  onSort: (key: string, order: SortOrder) => void;
  onPage: (offset: number) => void;
  onRowClick?: (row: T) => void;
  rowLabel?: (row: T) => string;
  getRowId: (row: T) => string;
}) {
  const t = useTranslations("table");
  const fmt = useFormat();
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table returns stable functions
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    getRowId,
  });
  const scroller = useRef<HTMLDivElement>(null);
  const rows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    initialRect: { width: 800, height: 480 },
  });
  const items = virtualizer.getVirtualItems();
  const before = items[0]?.start ?? 0;
  const after = virtualizer.getTotalSize() - (items.at(-1)?.end ?? 0);
  const colCount = columns.length;
  const last = Math.min(offset + limit, total);

  return (
    <div className="space-y-3">
      <div
        ref={scroller}
        className="max-h-[32rem] overflow-auto rounded-lg border bg-card"
        tabIndex={0}
        role="region"
        aria-label={caption}
        aria-busy={loading}
        data-testid="server-table"
      >
        <table className="w-full text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 z-10 bg-muted">
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const meta = header.column.columnDef.meta as ColumnMeta | undefined;
                  const active = meta?.sortKey === sort;
                  const label = flexRender(header.column.columnDef.header, header.getContext());
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : undefined}
                      className={cn("px-3 py-2 font-medium whitespace-nowrap", meta?.numeric ? "text-end" : "text-start")}
                    >
                      {meta?.sortKey ? (
                        <button
                          type="button"
                          className={cn("inline-flex items-center gap-1 hover:text-primary", meta.numeric && "flex-row-reverse")}
                          onClick={() =>
                            onSort(meta.sortKey as string, active && order === "desc" ? "asc" : "desc")
                          }
                          data-sort={meta.sortKey}
                        >
                          {label}
                          {active ? (
                            order === "asc" ? (
                              <ArrowUp className="size-3.5" aria-hidden="true" />
                            ) : (
                              <ArrowDown className="size-3.5" aria-hidden="true" />
                            )
                          ) : (
                            <ArrowUpDown className="size-3.5 text-muted-foreground" aria-hidden="true" />
                          )}
                        </button>
                      ) : (
                        label
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className={cn(loading && "opacity-60")}>
            {before > 0 ? (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: before }} />
              </tr>
            ) : null}
            {items.map((item) => {
              const row = rows[item.index];
              if (!row) return null;
              return (
                <tr
                  key={row.id}
                  className={cn("border-t", onRowClick && "cursor-pointer hover:bg-accent")}
                  style={{ height: ROW_HEIGHT }}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  data-row={row.id}
                >
                  {row.getVisibleCells().map((cell, i) => {
                    const meta = cell.column.columnDef.meta as ColumnMeta | undefined;
                    const content = flexRender(cell.column.columnDef.cell, cell.getContext());
                    return (
                      <td
                        key={cell.id}
                        className={cn("px-3 whitespace-nowrap", meta?.numeric ? "text-end tabular-nums" : "text-start")}
                      >
                        {i === 0 && onRowClick ? (
                          <button
                            type="button"
                            className="text-start text-primary underline-offset-4 hover:underline"
                            onClick={(e) => {
                              e.stopPropagation();
                              onRowClick(row.original);
                            }}
                            aria-label={rowLabel?.(row.original)}
                          >
                            {content}
                          </button>
                        ) : (
                          content
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
            {after > 0 ? (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: after }} />
              </tr>
            ) : null}
            {!rows.length ? (
              <tr>
                <td colSpan={colCount} className="px-3 py-8 text-center text-muted-foreground">
                  {t("empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <p className="text-muted-foreground" aria-live="polite" data-testid="table-range">
          {total
            ? t("range", { from: fmt.int(offset + 1), to: fmt.int(last), total: fmt.int(total) })
            : t("none")}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={offset === 0 || loading} onClick={() => onPage(Math.max(0, offset - limit))}>
            <ChevronLeft className="rtl:-scale-x-100" aria-hidden="true" />
            {t("previous")}
          </Button>
          <Button variant="outline" size="sm" disabled={last >= total || loading} onClick={() => onPage(offset + limit)}>
            {t("next")}
            <ChevronRight className="rtl:-scale-x-100" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  );
}
