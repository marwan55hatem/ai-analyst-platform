"use client";

import { BarChart3, Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { DatasetPicker } from "@/components/insights/analysis-gate";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useActiveDataset } from "@/lib/active-dataset";
import { isRunActive } from "@/lib/api/types";
import { useUrlUpdate } from "@/lib/url-state";

import { AlertsFeed } from "./alerts-feed";
import { GenerateDialog } from "./generate-dialog";
import { ReportList } from "./report-list";

type Tab = "reports" | "alerts";

/** Why a report cannot be generated yet, with the next step; null when it can. */
function GenerateBlocker({ active }: { active: ReturnType<typeof useActiveDataset> }) {
  const t = useTranslations("reports");
  if (active.loading) return <Skeleton className="h-20 w-full" />;
  if (active.error) return <ErrorState title={t("loadFailed")} error={active.error} onRetry={active.retry} />;
  if (!active.dataset) {
    return (
      <EmptyState
        icon={<BarChart3 />}
        title={t("noData.title")}
        body={t("noData.body")}
        action={
          <Link href="/datasets/new" className="text-primary underline underline-offset-4">
            {t("noData.cta")}
          </Link>
        }
      />
    );
  }
  const run = active.run;
  if (run && isRunActive(run.status)) {
    return (
      <p className="flex items-center gap-2 rounded-md bg-info-soft p-3 text-sm" role="status">
        <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        {t("runActive")}
      </p>
    );
  }
  if (!run || run.status === "failed") {
    return (
      <EmptyState
        icon={<BarChart3 />}
        title={t("noRun.title")}
        body={t("noRun.body")}
        action={
          <Link href={`/datasets/${active.dataset.id}`} className="text-primary underline underline-offset-4">
            {t("noRun.cta")}
          </Link>
        }
      />
    );
  }
  return null;
}

export function ReportsView() {
  const t = useTranslations("reports");
  const locale = useLocale() === "ar" ? "ar" : "en";
  const params = useSearchParams();
  const update = useUrlUpdate();
  const active = useActiveDataset();
  const tab: Tab = params.get("tab") === "alerts" ? "alerts" : "reports";
  const ready =
    !active.loading && active.dataset && active.run && !isRunActive(active.run.status) && active.run.status !== "failed";

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          tab === "reports" ? (
            <div className="flex flex-wrap items-end gap-3">
              <DatasetPicker active={active} />
              {ready && active.dataset ? (
                <GenerateDialog dataset={active.dataset} version={active.version} defaultLocale={locale} />
              ) : null}
            </div>
          ) : null
        }
      />
      <Tabs value={tab} onValueChange={(v) => update({ tab: v === "alerts" ? "alerts" : null })}>
        <TabsList>
          <TabsTrigger value="reports" data-testid="tab-reports">
            {t("tabs.reports")}
          </TabsTrigger>
          <TabsTrigger value="alerts" data-testid="tab-alerts">
            {t("tabs.alerts")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="reports" className="space-y-4 pt-4">
          <GenerateBlocker active={active} />
          <ReportList />
        </TabsContent>
        <TabsContent value="alerts" className="pt-4">
          <AlertsFeed />
        </TabsContent>
      </Tabs>
    </>
  );
}
