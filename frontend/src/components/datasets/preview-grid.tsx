"use client";

import { useTranslations } from "next-intl";

import type { Preview } from "@/lib/api/types";
import { useFormat } from "@/lib/format";

/** Render any cell as plain text. Values are untrusted and never parsed as HTML. */
export function cellText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** First rows of the current version, with a sticky header and scroll in both axes. */
export function PreviewGrid({ preview }: { preview: Preview }) {
  const t = useTranslations("preview");
  const tc = useTranslations("common");
  const fmt = useFormat();
  const total = preview.total_rows ?? preview.row_count;

  return (
    <div className="space-y-2">
      <div
        className="max-h-[28rem] overflow-auto rounded-lg border bg-card focus-visible:outline-2"
        tabIndex={0}
        role="region"
        aria-label={t("caption", { count: preview.rows.length, total: fmt.int(total) })}
      >
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">
            {t("caption", { count: preview.rows.length, total: fmt.int(total) })}
          </caption>
          <thead className="sticky top-0 z-10 bg-muted">
            <tr>
              <th scope="col" className="border-b px-3 py-2 text-end font-medium text-muted-foreground tabular-nums">
                #
              </th>
              {preview.columns.map((c) => (
                <th key={c.name} scope="col" className="border-b px-3 py-2 text-start font-medium whitespace-nowrap">
                  <bdi>{c.original || c.name}</bdi>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, i) => (
              <tr key={i} className="odd:bg-background/40">
                <td className="border-b px-3 py-1.5 text-end text-muted-foreground tabular-nums">{i + 1}</td>
                {preview.columns.map((c, j) => {
                  const text = cellText(row[j]);
                  return (
                    <td key={c.name} className="max-w-[18rem] truncate border-b px-3 py-1.5 whitespace-nowrap">
                      {text === null ? <span className="text-muted-foreground italic">{tc("emptyCell")}</span> : <bdi>{text}</bdi>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        {t("caption", { count: preview.rows.length, total: fmt.int(total) })}
      </p>
    </div>
  );
}
