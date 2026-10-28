"use client";

import { Info, Loader2, Play, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { isRunActive, type DatasetDetail, type DatasetVersion, type Fix } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import {
  useCleaningPlan,
  useDataset,
  useLatestRun,
  usePreview,
  useProfile,
  useQuality,
  useRerunProfile,
  useStartAnalysis,
} from "@/lib/queries";

import { AnalysisProgress } from "./analysis-progress";
import { CleaningPlan } from "./cleaning-plan";
import { ColumnsTable } from "./columns-table";
import { HealthPanel, IssueList } from "./health-panel";
import { PreviewGrid } from "./preview-grid";
import { StatusChip, chipState } from "./status-chip";

function Section({
  id,
  title,
  children,
  action,
}: {
  id: string;
  title: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-h`} id={id} className="scroll-mt-20 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-h`} tabIndex={-1} className="text-xl font-semibold outline-none">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function BusyBlock({ title, body }: { title: string; body: string }) {
  return (
    <div role="status" className="flex gap-4 rounded-lg border bg-card p-6">
      <Loader2 className="mt-0.5 size-6 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
    </div>
  );
}

function sourceScore(dataset: DatasetDetail, v: DatasetVersion): { version: number; score: number | null } | null {
  if (!v.source_version_id) return null;
  const source = dataset.versions?.find((x) => x.id === v.source_version_id);
  return source ? { version: source.version, score: source.quality_score } : null;
}

function ReadyView({ dataset, v }: { dataset: DatasetDetail; v: DatasetVersion }) {
  const t = useTranslations("detail");
  const ta = useTranslations("analysis");
  const tcol = useTranslations("columns");
  const errorMessage = useErrorMessage();
  const id = dataset.id;
  const quality = useQuality(id, v.version, true);
  const plan = useCleaningPlan(id, v.version, true);
  const profile = useProfile(id, v.version, true);
  const preview = usePreview(id, v.version, true);
  const run = useLatestRun(id, v.version, true);
  const start = useStartAnalysis(id, v.version);
  const [rolesChanged, setRolesChanged] = useState(false);
  const source = sourceScore(dataset, v);
  const active = isRunActive(run.data?.status);

  const runAnalysis = () => {
    start.mutate(undefined, {
      onSuccess: () => {
        setRolesChanged(false);
        toast.success(ta("started"));
        document.getElementById("analysis-h")?.focus();
      },
      onError: (error) => {
        if (error instanceof ApiError && error.code === "analysis_running") void run.refetch();
        toast.error(errorMessage(error));
      },
    });
  };

  const runButton = (
    <Button onClick={runAnalysis} disabled={start.isPending || active} data-testid="run-analysis">
      {start.isPending || active ? (
        <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <Play className="rtl:-scale-x-100" aria-hidden="true" />
      )}
      {start.isPending ? ta("starting") : run.data ? ta("runAgain") : ta("run")}
    </Button>
  );

  return (
    <>
      <PageHeader
        title={dataset.name}
        meta={<VersionMeta v={v} source={source?.version ?? null} />}
        actions={runButton}
      />
      <div className="space-y-10">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <Section id="health" title={t("sections.health")}>
            <div className="rounded-lg border bg-card p-5">
              {quality.isPending ? (
                <Skeleton className="h-56 w-full" />
              ) : quality.isError ? (
                <ErrorState title={t("loadError")} error={quality.error} onRetry={() => void quality.refetch()} />
              ) : (
                <HealthPanel quality={quality.data} previous={source?.score ?? null} />
              )}
            </div>
          </Section>
          <Section id="issues" title={t("sections.issues")}>
            {quality.isPending ? <Skeleton className="h-56 w-full" /> : quality.data ? <IssueList quality={quality.data} /> : null}
          </Section>
        </div>

        <Section id="plan" title={t("sections.plan")}>
          {plan.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : plan.isError ? (
            <ErrorState title={t("loadError")} error={plan.error} onRetry={() => void plan.refetch()} />
          ) : (
            <CleaningPlan key={plan.data.version_id} datasetId={id} version={v.version} fixes={plan.data.fixes as Fix[]} />
          )}
        </Section>

        <Section id="analysis" title={t("sections.analysis")}>
          <p className="max-w-prose text-sm text-muted-foreground">{ta("intro")}</p>
          {run.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : run.data ? (
            <AnalysisProgress run={run.data} />
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed bg-card p-5">
              <p className="text-muted-foreground">{ta("notRun")}</p>
              {runButton}
            </div>
          )}
        </Section>

        <Section id="columns" title={t("sections.columns")}>
          {rolesChanged ? (
            <div
              role="status"
              className="flex flex-col gap-3 rounded-lg border border-signal/50 bg-warn-soft p-4 sm:flex-row sm:items-center sm:justify-between"
              data-testid="rerun-prompt"
            >
              <div className="flex gap-3">
                <Info className="mt-0.5 size-5 shrink-0 text-signal" aria-hidden="true" />
                <div>
                  <p className="font-medium">{tcol("rolesChangedTitle")}</p>
                  <p className="text-sm">{tcol("rolesChangedBody")}</p>
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button variant="ghost" onClick={() => setRolesChanged(false)}>
                  {tcol("later")}
                </Button>
                <Button onClick={runAnalysis} disabled={start.isPending || active}>
                  <RotateCcw aria-hidden="true" />
                  {tcol("rerun")}
                </Button>
              </div>
            </div>
          ) : null}
          {profile.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : profile.isError ? (
            <ErrorState title={t("loadError")} error={profile.error} onRetry={() => void profile.refetch()} />
          ) : (
            <ColumnsTable
              datasetId={id}
              version={v.version}
              columns={profile.data.columns}
              onRoleChanged={() => setRolesChanged(true)}
            />
          )}
        </Section>

        <Section id="preview" title={t("sections.preview")}>
          {preview.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : preview.isError ? (
            <ErrorState title={t("loadError")} error={preview.error} onRetry={() => void preview.refetch()} />
          ) : (
            <PreviewGrid preview={preview.data} />
          )}
        </Section>
      </div>
    </>
  );
}

function VersionMeta({ v, source }: { v: DatasetVersion; source: number | null }) {
  const t = useTranslations("detail");
  const tc = useTranslations("common");
  const fmt = useFormat();
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <StatusChip state={chipState(v)} />
      <span className="font-medium text-foreground" data-testid="version">
        {tc("version", { version: v.version })}
      </span>
      <span>
        {source !== null ? t("cleanedFrom", { version: source }) : t("originalFile", { file: v.original_filename })}
      </span>
      {v.row_count !== null ? <span>{tc("rows", { count: v.row_count })}</span> : null}
      <span>
        <time dateTime={v.created_at}>{t("uploadedAt", { when: fmt.ago(v.created_at) })}</time>
      </span>
    </span>
  );
}

/** Data health view of a dataset's latest version. */
export function DatasetDetailView({ id }: { id: string }) {
  const t = useTranslations("detail");
  const tc = useTranslations("common");
  const { data, isPending, isError, error, refetch } = useDataset(id);
  const v = data?.latest_version ?? null;
  const rerun = useRerunProfile(id, v?.version ?? 1);
  const errorMessage = useErrorMessage();

  if (isPending) {
    return (
      <div className="space-y-6" aria-busy="true">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="space-y-4">
        <ErrorState title={t("loadError")} error={error} onRetry={() => void refetch()} />
        <Button variant="outline" asChild>
          <Link href="/datasets">{tc("backToDatasets")}</Link>
        </Button>
      </div>
    );
  }
  if (!v) return null;

  const state = chipState(v);
  if (state === "ready" && v.profile_status === "ready") return <ReadyView key={v.id} dataset={data} v={v} />;

  return (
    <>
      <PageHeader title={data.name} meta={<VersionMeta v={v} source={null} />} />
      {state === "processing" ? (
        <BusyBlock title={t("processingTitle")} body={t("processingBody")} />
      ) : state === "checking" ? (
        <BusyBlock title={t("checkingTitle")} body={t("checkingBody")} />
      ) : state === "failed" ? (
        <ErrorState title={t("failedTitle")}>
          {v.error ? (
            <p className="text-sm">
              <span lang="en" dir="ltr">
                {v.error}
              </span>
            </p>
          ) : null}
          <p className="text-sm">{t("failedHelp")}</p>
          <Button variant="outline" size="sm" asChild>
            <Link href="/datasets/new">{t("uploadAgain")}</Link>
          </Button>
        </ErrorState>
      ) : (
        <ErrorState title={t("profileFailedTitle")}>
          {v.profile_error ? (
            <p className="text-sm">
              <span lang="en" dir="ltr">
                {v.profile_error}
              </span>
            </p>
          ) : null}
          <Button
            size="sm"
            disabled={rerun.isPending}
            onClick={() => rerun.mutate(undefined, { onError: (e) => toast.error(errorMessage(e)) })}
          >
            {t("rerunProfile")}
          </Button>
        </ErrorState>
      )}
    </>
  );
}
