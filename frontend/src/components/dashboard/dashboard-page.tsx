"use client";

import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/common/page-header";
import { AnalysisGate, DatasetPicker } from "@/components/insights/analysis-gate";
import { useActiveDataset } from "@/lib/active-dataset";

import { DashboardView } from "./dashboard-view";

/** /dashboard: headline KPIs for a date range, the revenue trend and what needs attention. */
export function DashboardPage() {
  const t = useTranslations("dashboard");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader
        title={t("title")}
        description={active.dataset ? t("description", { name: active.dataset.name }) : t("descriptionEmpty")}
        actions={<DatasetPicker active={active} />}
      />
      <AnalysisGate active={active}>
        {(run) =>
          active.dataset ? (
            <DashboardView key={active.dataset.id} run={run} datasetId={active.dataset.id} version={active.version} />
          ) : null
        }
      </AnalysisGate>
    </>
  );
}
