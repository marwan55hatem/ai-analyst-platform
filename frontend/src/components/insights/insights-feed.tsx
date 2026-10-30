"use client";

import { Lightbulb } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveDataset } from "@/lib/active-dataset";
import { MODULES, type AnalysisRun, type Confidence } from "@/lib/api/types";
import { useInsights, type InsightFilters } from "@/lib/queries";
import { useUrlUpdate } from "@/lib/url-state";

import { AnalysisGate, DatasetPicker } from "./analysis-gate";
import { InsightDrawer } from "./insight-drawer";
import { InsightList } from "./insight-list";

const ALL = "all";
const CONFIDENCE: Confidence[] = ["high", "medium", "low"];

/** /insights: every finding of the run ranked by impact, with filters and the evidence drawer. */
export function InsightsFeed() {
  const t = useTranslations("insights");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<DatasetPicker active={active} />} />
      <AnalysisGate active={active}>{(run) => <Feed run={run} />}</AnalysisGate>
    </>
  );
}

function Feed({ run }: { run: AnalysisRun }) {
  const t = useTranslations("insights");
  const params = useSearchParams();
  const moduleParam = params.get("module");
  const confidenceParam = params.get("confidence");
  const filters: InsightFilters = {
    module: moduleParam && (MODULES as readonly string[]).includes(moduleParam) ? moduleParam : undefined,
    confidence: CONFIDENCE.includes(confidenceParam as Confidence) ? (confidenceParam as Confidence) : undefined,
  };
  const found = useInsights(run.id, filters);
  const open = params.get("insight");

  const update = useUrlUpdate();
  const set = (key: string, value: string | null) => update({ [key]: value === ALL ? null : value });

  const items = found.data?.pages.flat() ?? [];
  const filtered = Boolean(filters.module || filters.confidence);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-card p-3" role="group" aria-label={t("filters")}>
        <div className="space-y-1">
          <Label htmlFor="filter-module" className="text-xs text-muted-foreground">
            {t("filterType")}
          </Label>
          <Select value={filters.module ?? ALL} onValueChange={(v) => set("module", v)}>
            <SelectTrigger id="filter-module" className="w-52" data-testid="filter-module">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("allTypes")}</SelectItem>
              {MODULES.map((m) => (
                <SelectItem key={m} value={m}>
                  {t(`modules.${m}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="filter-confidence" className="text-xs text-muted-foreground">
            {t("filterConfidence")}
          </Label>
          <Select value={filters.confidence ?? ALL} onValueChange={(v) => set("confidence", v)}>
            <SelectTrigger id="filter-confidence" className="w-44" data-testid="filter-confidence">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("allConfidence")}</SelectItem>
              {CONFIDENCE.map((c) => (
                <SelectItem key={c} value={c}>
                  {t(`confidence.${c}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {filtered ? (
          <Button variant="ghost" size="sm" onClick={() => update({ module: null, confidence: null })}>
            {t("clearFilters")}
          </Button>
        ) : null}
      </div>

      {found.isPending ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : found.error ? (
        <ErrorState title={t("loadFailed")} error={found.error} onRetry={() => void found.refetch()} />
      ) : items.length ? (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {t("shown", { count: items.length })}
          </p>
          <InsightList insights={items} onOpen={(id) => set("insight", id)} />
          {found.hasNextPage ? (
            <Button variant="outline" onClick={() => void found.fetchNextPage()} disabled={found.isFetchingNextPage}>
              {found.isFetchingNextPage ? t("loadingMore") : t("loadMore")}
            </Button>
          ) : null}
        </>
      ) : (
        <EmptyState
          icon={<Lightbulb />}
          title={filtered ? t("emptyFiltered.title") : t("empty.title")}
          body={filtered ? t("emptyFiltered.body") : t("empty.body")}
        />
      )}
      <InsightDrawer id={open} onClose={() => set("insight", null)} />
    </div>
  );
}
