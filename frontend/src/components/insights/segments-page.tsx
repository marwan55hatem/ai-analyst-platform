"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { ChartFrame } from "@/components/charts/chart-frame";
import { Heatmap } from "@/components/charts/heatmap";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveDataset } from "@/lib/active-dataset";
import {
  SEGMENT_NAMES,
  type AnalysisRun,
  type SegmentAssignment,
  type SegmentName,
  type SegmentSort,
  type SortOrder,
} from "@/lib/api/types";
import { rfmGrid, type RfmCell } from "@/lib/charts/rfm";
import { useFormat } from "@/lib/format";
import { useSegments } from "@/lib/queries";

import { AnalysisGate, DatasetPicker, ModuleNotice } from "./analysis-gate";
import { EngineText, FactBadge } from "./badges";
import { ExportButton } from "./export-button";
import { ServerTable, type ColumnMeta } from "./server-table";

const PAGE = 100;
const ALL = "all";

interface SegmentRow {
  segment: string;
  customers: number;
  revenue: number;
  revenue_share_pct: number;
  avg_spend: number;
  avg_orders: number;
  avg_recency_days: number;
  recommended_action?: { text: string };
}

/** /insights/segments: who the customers are (RFM), what each group is worth, what to do. */
export function SegmentsPage() {
  const t = useTranslations("segments");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<DatasetPicker active={active} />} />
      <AnalysisGate active={active}>
        {(run) =>
          active.dataset ? <SegmentsView run={run} datasetId={active.dataset.id} version={active.version} /> : null
        }
      </AnalysisGate>
    </>
  );
}

function SegmentsView({ run, datasetId, version }: { run: AnalysisRun; datasetId: string; version: number }) {
  const t = useTranslations("segments");
  const fmt = useFormat();
  const [segment, setSegment] = useState<SegmentName | undefined>();
  const [sort, setSort] = useState<SegmentSort>("monetary");
  const [order, setOrder] = useState<SortOrder>("desc");
  const [offset, setOffset] = useState(0);
  const found = useSegments(run.id, { segment, sort, order, limit: PAGE, offset });
  const grid = useMemo(() => rfmGrid((found.data?.rfm_grid ?? []) as RfmCell[]), [found.data?.rfm_grid]);
  const segments = (found.data?.segments ?? []) as unknown as SegmentRow[];

  const columns = useMemo<ColumnDef<SegmentAssignment>[]>(
    () => [
      { accessorKey: "customer_key", header: t("col.customer"), meta: { sortKey: "customer_key" } satisfies ColumnMeta, cell: (c) => <bdi>{c.getValue<string>()}</bdi> },
      { accessorKey: "rfm_segment", header: t("col.segment"), cell: (c) => t.has(`names.${c.getValue<string>()}`) ? t(`names.${c.getValue<string>()}`) : c.getValue<string>() },
      { accessorKey: "monetary", header: t("col.spend"), meta: { sortKey: "monetary", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.money(c.getValue<number>()) },
      { accessorKey: "frequency", header: t("col.orders"), meta: { sortKey: "frequency", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.int(c.getValue<number>()) },
      { accessorKey: "recency_days", header: t("col.recency"), meta: { sortKey: "recency_days", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.int(c.getValue<number>()) },
      { id: "rfm", header: t("col.rfm"), meta: { numeric: true } satisfies ColumnMeta, cell: (c) => `${c.row.original.r_score}-${c.row.original.f_score}-${c.row.original.m_score}` },
      { accessorKey: "cluster_name", header: t("col.cluster"), cell: (c) => <EngineText>{c.getValue<string | null>() ?? "—"}</EngineText> },
    ],
    [t, fmt],
  );

  if (run.progress.modules?.segmentation?.status !== "completed") {
    return <ModuleNotice run={run} module="segmentation" datasetId={datasetId} version={version} />;
  }
  if (found.isPending) return <Skeleton className="h-96 w-full" />;
  if (found.error) return <ErrorState title={t("loadFailed")} error={found.error} onRetry={() => void found.refetch()} />;
  const data = found.data;
  const top = [...segments].sort((a, b) => b.revenue_share_pct - a.revenue_share_pct)[0];
  const totalCustomers = segments.reduce((s, x) => s + x.customers, 0);

  return (
    <div className="space-y-8">
      {top ? (
        <p className="max-w-prose text-lg" data-testid="segments-answer">
          {t("answer", {
            segment: t.has(`names.${top.segment}`) ? t(`names.${top.segment}`) : top.segment,
            share: fmt.dec(top.revenue_share_pct),
            customers: fmt.int(top.customers),
            pct: fmt.dec(totalCustomers ? (100 * top.customers) / totalCustomers : 0),
          })}
        </p>
      ) : null}

      <section aria-labelledby="segment-cards" className="space-y-3">
        <h2 id="segment-cards" className="text-lg font-semibold">
          {t("cards")}
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {segments.map((s) => (
            <li key={s.segment} className="space-y-2 rounded-lg border bg-card p-4" data-testid="segment-card">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-semibold">{t.has(`names.${s.segment}`) ? t(`names.${s.segment}`) : s.segment}</h3>
                <span className="text-sm text-muted-foreground tabular-nums">
                  {t("customers", { n: fmt.int(s.customers) })}
                </span>
              </div>
              <p className="text-2xl font-semibold tabular-nums">
                {fmt.dec(s.revenue_share_pct)}%
                <span className="ms-2 text-sm font-normal text-muted-foreground">{t("ofRevenue")}</span>
              </p>
              <div className="h-1.5 rounded-full bg-muted" aria-hidden="true">
                <div className="h-full rounded-full bg-chart-actual" style={{ width: `${Math.min(100, s.revenue_share_pct)}%` }} />
              </div>
              <dl className="grid grid-cols-3 gap-2 text-xs">
                <div>
                  <dt className="text-muted-foreground">{t("avgSpend")}</dt>
                  <dd className="tabular-nums">{fmt.compact(s.avg_spend)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("avgOrders")}</dt>
                  <dd className="tabular-nums">{fmt.dec(s.avg_orders)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("avgRecency")}</dt>
                  <dd className="tabular-nums">{t("days", { n: fmt.int(s.avg_recency_days) })}</dd>
                </div>
              </dl>
              {s.recommended_action?.text ? (
                <p className="border-s-2 border-primary ps-3 text-sm">
                  <span className="block text-xs text-muted-foreground">{t("action")}</span>
                  <EngineText>{s.recommended_action.text.replace(/^Suggestion:\s*/, "")}</EngineText>
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <ChartFrame
        testId="rfm-grid"
        title={t("grid.title")}
        takeaway={t("grid.takeaway")}
        badges={data.k ? <FactBadge>{t("clusters", { k: data.k, ari: fmt.dec2(Number((data.stability as { median_ari?: number } | null)?.median_ari ?? 0)) })}</FactBadge> : null}
      >
        <Heatmap
          rows={grid}
          caption={t("grid.caption")}
          rowTitle={t("grid.recency")}
          colTitle={t("grid.frequency")}
          rowLabel={(r) => t("grid.r", { r })}
          colLabel={(c) => t("grid.f", { f: c })}
          cellText={(c) => (c.customers ? fmt.int(c.customers) : "–")}
          cellTooltip={(c) => t("grid.tooltip", { n: fmt.int(c.customers), pct: fmt.dec(c.share), r: c.row, f: c.col })}
          legend={{ low: t("grid.fewer"), high: t("grid.more") }}
        />
      </ChartFrame>

      <section aria-labelledby="customers" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h2 id="customers" className="text-lg font-semibold">
              {t("table")}
            </h2>
            {data.pseudonymized_customer_ids ? <p className="text-sm text-muted-foreground">{t("pseudonyms")}</p> : null}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="segment-filter" className="text-xs text-muted-foreground">
                {t("filter")}
              </Label>
              <Select
                value={segment ?? ALL}
                onValueChange={(v) => {
                  setSegment(v === ALL ? undefined : (v as SegmentName));
                  setOffset(0);
                }}
              >
                <SelectTrigger id="segment-filter" className="w-48" data-testid="segment-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("allSegments")}</SelectItem>
                  {SEGMENT_NAMES.map((n) => (
                    <SelectItem key={n} value={n}>
                      {t(`names.${n}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <ExportButton
              path={`/api/v1/analysis-runs/${run.id}/segments.csv`}
              params={{ segment, sort, order }}
              fallback="segments.csv"
            />
          </div>
        </div>
        <ServerTable
          columns={columns}
          data={data.assignments.items}
          total={data.assignments.total}
          offset={offset}
          limit={PAGE}
          sort={sort}
          order={order}
          caption={t("table")}
          loading={found.isFetching}
          getRowId={(r) => r.customer_key}
          onSort={(key, o) => {
            setSort(key as SegmentSort);
            setOrder(o);
            setOffset(0);
          }}
          onPage={setOffset}
        />
      </section>
    </div>
  );
}
