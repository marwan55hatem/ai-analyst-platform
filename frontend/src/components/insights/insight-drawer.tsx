"use client";

import { MessagesSquare } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useId } from "react";

import { DataTable } from "@/components/charts/chart-frame";
import { CopyButton } from "@/components/common/copy-button";
import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { withDataset } from "@/lib/active-dataset";
import type { EvidenceQuery, Insight } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { useInsight } from "@/lib/queries";
import { useReturnFocus } from "@/lib/return-focus";

import { ConfidenceBadge, EngineText, SeverityBadge } from "./badges";

/** Text of one evidence cell (numbers keep their digits; nulls show as a dash). */
function cell(value: unknown): string | number {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value;
  return String(value);
}

/** One query behind an insight: what it shows, the exact SQL and its first rows. */
export function EvidenceBlock({ query }: { query: EvidenceQuery }) {
  const t = useTranslations("insights");
  const fmt = useFormat();
  const heading = useId();
  const numeric = query.rows[0]?.map((v, i) => (typeof v === "number" ? i : -1)).filter((i) => i >= 0) ?? [];
  return (
    <section className="space-y-2" aria-labelledby={heading} data-testid="evidence">
      <h4 id={heading} className="text-sm font-medium">
        <EngineText>{query.purpose}</EngineText>
      </h4>
      <div className="rounded-md border bg-muted">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-1">
          <span className="text-xs text-muted-foreground">{t("evidence.sql")}</span>
          <CopyButton text={query.sql} />
        </div>
        <pre className="max-h-48 overflow-auto p-3 text-xs leading-relaxed whitespace-pre-wrap break-words" dir="ltr" tabIndex={0}>
          <code>{query.sql}</code>
        </pre>
      </div>
      {query.columns.length ? (
        <>
          <DataTable
            table={{ columns: query.columns, rows: query.rows.map((r) => r.map(cell)), numeric }}
            caption={query.purpose}
          />
          <p className="text-xs text-muted-foreground">
            {t("evidence.rows", { shown: fmt.int(query.rows.length), total: fmt.int(query.row_count) })}
          </p>
        </>
      ) : null}
    </section>
  );
}

/** The insight's own facts: value, method, warnings, then every query as evidence. */
export function InsightDetail({ insight }: { insight: Insight }) {
  const t = useTranslations("insights");
  const queries = insight.evidence.queries ?? [];
  const warnings = insight.payload.warnings ?? [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        <SeverityBadge impact={insight.impact_score} />
        <ConfidenceBadge level={insight.confidence} sampleSize={insight.sample_size} />
      </div>
      <p>
        <EngineText>{insight.summary}</EngineText>
      </p>
      {insight.payload.method ? (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">{t("method")}</h3>
          <p className="text-sm text-muted-foreground">
            <EngineText>{insight.payload.method}</EngineText>
          </p>
        </div>
      ) : null}
      {warnings.length ? (
        <div className="space-y-1 rounded-md border border-warn/40 bg-warn-soft p-3" role="note">
          <h3 className="text-sm font-medium text-warn">{t("warnings")}</h3>
          <ul className="list-disc space-y-1 ps-5 text-sm">
            {warnings.map((w) => (
              <li key={w}>
                <EngineText>{w}</EngineText>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="space-y-4">
        <h3 className="text-base font-semibold">{t("evidence.title")}</h3>
        {queries.length ? (
          queries.map((q, i) => <EvidenceBlock key={i} query={q} />)
        ) : (
          <p className="text-sm text-muted-foreground">{t("evidence.none")}</p>
        )}
      </div>
    </div>
  );
}

/** Side drawer with one insight's evidence and a way to ask the analyst about it. */
export function InsightDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const t = useTranslations("insights");
  const found = useInsight(id);
  const returnFocus = useReturnFocus();
  const dataset = useSearchParams().get("dataset") ?? undefined;
  return (
    <Sheet open={Boolean(id)} onOpenChange={(open) => (open ? null : onClose())}>
      <SheetContent
        side="end"
        className="w-full overflow-y-auto sm:max-w-xl"
        closeLabel={t("drawer.close")}
        {...returnFocus}
        data-testid="insight-drawer"
      >
        <SheetHeader className="space-y-1 pe-10">
          <SheetTitle className="text-lg">
            {found.data ? <EngineText>{found.data.title}</EngineText> : t("drawer.loading")}
          </SheetTitle>
          <SheetDescription>{t("drawer.description")}</SheetDescription>
        </SheetHeader>
        <div className="space-y-6 px-4 pb-6">
          {found.isPending ? (
            <div className="space-y-3">
              <Skeleton className="h-6 w-1/2" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : found.error ? (
            <ErrorState title={t("drawer.failed")} error={found.error} onRetry={() => void found.refetch()} />
          ) : found.data ? (
            <>
              <Button asChild className="w-full sm:w-auto" data-testid="ask-analyst">
                <Link href={withDataset(`/chat?insight=${found.data.id}`, dataset)}>
                  <MessagesSquare aria-hidden="true" />
                  {t("ask")}
                </Link>
              </Button>
              <InsightDetail insight={found.data} />
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
