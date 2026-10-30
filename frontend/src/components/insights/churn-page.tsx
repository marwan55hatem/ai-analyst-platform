"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { BarChart } from "@/components/charts/bar-chart";
import { ChartFrame } from "@/components/charts/chart-frame";
import { SignedBars } from "@/components/charts/range-bar";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveDataset } from "@/lib/active-dataset";
import type { AnalysisRun, ChurnScore, ChurnSort, SortOrder } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { useChurn, type ChurnQuery } from "@/lib/queries";
import { useReturnFocus } from "@/lib/return-focus";
import { cn } from "@/lib/utils";

import { AnalysisGate, DatasetPicker, ModuleNotice } from "./analysis-gate";
import { FactBadge } from "./badges";
import { ExportButton } from "./export-button";
import { ServerTable, type ColumnMeta } from "./server-table";

const PAGE = 100;
const ALL = "all";
const BANDS = ["high", "medium", "low"] as const;
type BandName = (typeof BANDS)[number];

const BAND_STYLE: Record<BandName, string> = {
  high: "bg-bad-soft text-bad",
  medium: "bg-warn-soft text-warn",
  low: "bg-good-soft text-good",
};

/** /insights/churn: who may stop buying, how much revenue is at stake, and why. */
export function ChurnPage() {
  const t = useTranslations("churn");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<DatasetPicker active={active} />} />
      <AnalysisGate active={active}>
        {(run) =>
          active.dataset ? <ChurnView run={run} datasetId={active.dataset.id} version={active.version} /> : null
        }
      </AnalysisGate>
    </>
  );
}

function BandBadge({ band }: { band: BandName }) {
  const t = useTranslations("churn");
  return (
    <span className={cn("inline-flex rounded-full px-2 py-0.5 text-xs font-medium", BAND_STYLE[band])} data-band={band}>
      {t(`band.${band}`)}
    </span>
  );
}

function ChurnView({ run, datasetId, version }: { run: AnalysisRun; datasetId: string; version: number }) {
  const t = useTranslations("churn");
  const fmt = useFormat();
  const [query, setQuery] = useState<ChurnQuery>({ sort: "expected_loss", order: "desc", limit: PAGE, offset: 0 });
  const [selected, setSelected] = useState<ChurnScore | null>(null);
  const found = useChurn(run.id, query);
  const returnFocus = useReturnFocus();

  const columns = useMemo<ColumnDef<ChurnScore>[]>(
    () => [
      { accessorKey: "customer_key", header: t("col.customer"), cell: (c) => <bdi>{c.getValue<string>()}</bdi> },
      { accessorKey: "probability", header: t("col.probability"), meta: { sortKey: "probability", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.pct(c.getValue<number>()) },
      { accessorKey: "risk_band", header: t("col.band"), cell: (c) => <BandBadge band={c.getValue<BandName>()} /> },
      { accessorKey: "revenue_at_stake", header: t("col.stake"), meta: { sortKey: "revenue_at_stake", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.money(c.getValue<number>()) },
      { accessorKey: "expected_loss", header: t("col.loss"), meta: { sortKey: "expected_loss", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.money(c.getValue<number>()) },
      { accessorKey: "recency_days", header: t("col.recency"), meta: { sortKey: "recency_days", numeric: true } satisfies ColumnMeta, cell: (c) => fmt.int(c.getValue<number>()) },
    ],
    [t, fmt],
  );

  if (run.progress.modules?.churn?.status !== "completed") {
    return <ModuleNotice run={run} module="churn" datasetId={datasetId} version={version} />;
  }
  if (found.isPending) return <Skeleton className="h-96 w-full" />;
  if (found.error) return <ErrorState title={t("loadFailed")} error={found.error} onRetry={() => void found.refetch()} />;
  const data = found.data;
  const high = (data.bands as Record<string, { customers?: number; yearly_spend?: number; expected_loss?: number }>).high ?? {};
  const importance = (data.importance ?? []) as { label: string; feature: string; share_pct: number }[];
  const model = data.model as { winner?: string; validation?: string; models?: Record<string, { auc?: number }> } | null;
  const auc = model?.winner ? model.models?.[model.winner]?.auc : undefined;
  const set = (patch: Partial<ChurnQuery>) => setQuery((q) => ({ ...q, offset: 0, ...patch }));

  return (
    <div className="space-y-8">
      <section aria-labelledby="churn-answer" className="grid gap-3 sm:grid-cols-3" data-testid="churn-answer">
        <h2 id="churn-answer" className="sr-only">
          {t("summary")}
        </h2>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">{t("highRisk")}</p>
          <p className="text-2xl font-semibold tabular-nums">{fmt.int(high.customers ?? 0)}</p>
          <p className="text-sm text-muted-foreground">{t("ofCustomers", { total: fmt.int(data.customers.total) })}</p>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">{t("yearlySpend")}</p>
          <p className="text-2xl font-semibold tabular-nums">{fmt.compact(high.yearly_spend ?? 0)}</p>
          <p className="text-sm text-muted-foreground">{t("yearlySpendHelp")}</p>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">{t("expectedLoss")}</p>
          <p className="text-2xl font-semibold tabular-nums">{fmt.compact(high.expected_loss ?? 0)}</p>
          <p className="text-sm text-muted-foreground">{t("expectedLossHelp")}</p>
        </div>
      </section>
      <div className="flex flex-wrap gap-2">
        {auc !== undefined ? <FactBadge>{t("modelCheck", { auc: fmt.dec(auc * 100) })}</FactBadge> : null}
        {model?.validation === "out_of_time" ? <FactBadge>{t("outOfTime")}</FactBadge> : null}
        <p className="w-full text-sm text-muted-foreground">{t("estimateNote")}</p>
      </div>

      <section aria-labelledby="at-risk" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h2 id="at-risk" className="text-lg font-semibold">
              {t("table")}
            </h2>
            <p className="text-sm text-muted-foreground">{t("tableHelp")}</p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="band-filter" className="text-xs text-muted-foreground">
                {t("filter")}
              </Label>
              <Select value={query.risk_band ?? ALL} onValueChange={(v) => set({ risk_band: v === ALL ? undefined : (v as BandName) })}>
                <SelectTrigger id="band-filter" className="w-44" data-testid="band-filter">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("allBands")}</SelectItem>
                  {BANDS.map((b) => (
                    <SelectItem key={b} value={b}>
                      {t(`band.${b}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <ExportButton
              path={`/api/v1/analysis-runs/${run.id}/churn.csv`}
              params={{ risk_band: query.risk_band, sort: query.sort, order: query.order }}
              fallback="churn.csv"
            />
          </div>
        </div>
        <ServerTable
          columns={columns}
          data={data.customers.items}
          total={data.customers.total}
          offset={query.offset}
          limit={PAGE}
          sort={query.sort}
          order={query.order}
          caption={t("table")}
          loading={found.isFetching}
          getRowId={(r) => r.customer_key}
          onSort={(key, order: SortOrder) => set({ sort: key as ChurnSort, order })}
          onPage={(offset) => setQuery((q) => ({ ...q, offset }))}
          onRowClick={setSelected}
          rowLabel={(r) => t("openCustomer", { customer: r.customer_key })}
        />
      </section>

      <ChartFrame
        testId="churn-drivers"
        title={t("drivers.title")}
        takeaway={importance[0] ? t("drivers.takeaway", { label: importance[0].label, pct: fmt.dec(importance[0].share_pct) }) : undefined}
        unit={t("drivers.unit")}
        table={{
          columns: [t("drivers.factor"), t("drivers.share")],
          numeric: [1],
          rows: importance.map((i) => [i.label, `${fmt.dec(i.share_pct)}%`]),
        }}
      >
        <BarChart
          data={importance.map((i) => ({ label: i.label, value: i.share_pct }))}
          formatValue={(n) => `${fmt.dec(n)}%`}
          ariaLabel={t("drivers.title")}
        />
      </ChartFrame>

      <Sheet open={Boolean(selected)} onOpenChange={(open) => (open ? null : setSelected(null))}>
        <SheetContent
          side="end"
          className="w-full overflow-y-auto sm:max-w-md"
          closeLabel={t("panel.close")}
          {...returnFocus}
          data-testid="customer-panel"
        >
          {selected ? <CustomerPanel score={selected} /> : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function CustomerPanel({ score }: { score: ChurnScore }) {
  const t = useTranslations("churn");
  const fmt = useFormat();
  const max = Math.max(0, ...score.reasons.map((r) => Math.abs(r.shap)));
  return (
    <>
      <SheetHeader className="pe-10">
        <SheetTitle>
          <bdi>{score.customer_key}</bdi>
        </SheetTitle>
        <SheetDescription>{t("panel.description")}</SheetDescription>
      </SheetHeader>
      <div className="space-y-6 px-4 pb-6">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-3xl font-semibold tabular-nums">{fmt.pct(score.probability)}</p>
          <BandBadge band={score.risk_band} />
        </div>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-muted-foreground">{t("col.stake")}</dt>
            <dd className="tabular-nums">{fmt.money(score.revenue_at_stake)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("col.loss")}</dt>
            <dd className="tabular-nums">{fmt.money(score.expected_loss)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("col.recency")}</dt>
            <dd className="tabular-nums">{fmt.int(score.recency_days)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("panel.orders")}</dt>
            <dd className="tabular-nums">{fmt.int(score.frequency)}</dd>
          </div>
        </dl>
        <section className="space-y-3" aria-labelledby="reasons">
          <h3 id="reasons" className="font-semibold">
            {t("panel.reasons")}
          </h3>
          <SignedBars
            max={max}
            items={score.reasons.map((r) => ({
              key: r.feature,
              label: r.text,
              value: r.shap,
              tone: r.effect === "raises" ? "up" : "down",
              word: t(`panel.${r.effect}`),
            }))}
          />
          <p className="text-xs text-muted-foreground">{t("panel.help")}</p>
        </section>
        <p className="text-sm text-muted-foreground">{t("panel.note")}</p>
      </div>
    </>
  );
}
