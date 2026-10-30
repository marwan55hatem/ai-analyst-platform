"use client";

import { BarChart3, CircleSlash, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { ActiveDataset } from "@/lib/active-dataset";
import { isRunActive, type AnalysisRun, type Capabilities } from "@/lib/api/types";
import { useCapabilities } from "@/lib/queries";

import { EngineText } from "./badges";

/** Dataset chooser shown in page headers (only when there is more than one). */
export function DatasetPicker({ active }: { active: ActiveDataset }) {
  const t = useTranslations("insights");
  if (active.datasets.length < 2 || !active.dataset) return null;
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor="dataset-picker" className="text-xs text-muted-foreground">
        {t("dataset")}
      </Label>
      <Select value={active.dataset.id} onValueChange={active.select}>
        <SelectTrigger id="dataset-picker" className="w-56 max-w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {active.datasets.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              <bdi>{d.name}</bdi>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/**
 * Renders its children only when the active dataset has a finished analysis run; otherwise it
 * says honestly why there is nothing to show yet and what to do.
 */
export function AnalysisGate({
  active,
  children,
}: {
  active: ActiveDataset;
  children: (run: AnalysisRun) => ReactNode;
}) {
  const t = useTranslations("insights");
  if (active.loading) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label={t("loading")}>
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (active.error) {
    return <ErrorState title={t("loadFailed")} error={active.error} onRetry={active.retry} />;
  }
  if (!active.dataset) {
    return (
      <EmptyState
        icon={<BarChart3 />}
        title={t("noData.title")}
        body={t("noData.body")}
        action={
          <Button asChild>
            <Link href="/datasets/new">{t("noData.cta")}</Link>
          </Button>
        }
      />
    );
  }
  const run = active.run;
  const detail = `/datasets/${active.dataset.id}`;
  if (!run) {
    return (
      <EmptyState
        icon={<BarChart3 />}
        title={t("noRun.title")}
        body={t("noRun.body")}
        action={
          <Button asChild>
            <Link href={detail}>{t("noRun.cta")}</Link>
          </Button>
        }
      />
    );
  }
  if (isRunActive(run.status)) {
    return (
      <EmptyState
        icon={<Loader2 className="animate-spin motion-reduce:animate-none" />}
        title={t("running.title")}
        body={t("running.body", { done: run.progress.done ?? 0, total: run.progress.total ?? 9 })}
        action={
          <Button asChild variant="outline">
            <Link href={detail}>{t("running.cta")}</Link>
          </Button>
        }
      />
    );
  }
  if (run.status === "failed") {
    return (
      <ErrorState title={t("failed.title")}>
        <p className="text-sm">{t("failed.body")}</p>
        <Button asChild variant="outline" size="sm">
          <Link href={detail}>{t("failed.cta")}</Link>
        </Button>
      </ErrorState>
    );
  }
  return <>{children(run)}</>;
}

/** What a skipped module needs, from the run (why it was skipped) and the capability check. */
export function moduleProblem(
  run: AnalysisRun,
  module: string,
  capabilities?: Capabilities,
): { state: "skipped" | "failed"; reason: string | null } | null {
  const state = run.progress.modules?.[module];
  if (!state || state.status === "completed") return null;
  if (state.status === "failed") return { state: "failed", reason: null };
  const capability = capabilities?.modules.find((m) => m.name === module);
  return { state: "skipped", reason: state.reason || capability?.reason || null };
}

/**
 * Honest notice for a module that did not produce results: the engine's reason and, in the
 * user's language, what data would let it run. Renders nothing for a completed module.
 */
export function ModuleNotice({
  run,
  module,
  datasetId,
  version,
}: {
  run: AnalysisRun;
  module: string;
  datasetId: string;
  version: number;
}) {
  const t = useTranslations("insights");
  const state = run.progress.modules?.[module];
  const caps = useCapabilities(datasetId, version, state?.status === "skipped");
  const problem = moduleProblem(run, module, caps.data);
  if (!problem) return null;
  const Icon = problem.state === "failed" ? XCircle : CircleSlash;
  return (
    <div
      className="flex gap-3 rounded-lg border border-dashed bg-card p-5"
      data-testid="module-notice"
      data-module={module}
      data-state={problem.state}
    >
      <Icon className={problem.state === "failed" ? "mt-0.5 size-5 shrink-0 text-bad" : "mt-0.5 size-5 shrink-0 text-warn"} aria-hidden="true" />
      <div className="min-w-0 space-y-2">
        <h2 className="font-semibold">{t(`notice.${problem.state}`, { module: t(`modules.${module}`) })}</h2>
        {problem.reason ? (
          <p className="text-sm">
            <span className="text-muted-foreground">{t("notice.reason")} </span>
            <EngineText>{problem.reason}</EngineText>
          </p>
        ) : null}
        {problem.state === "skipped" ? (
          <p className="max-w-prose text-sm text-muted-foreground">{t(`needs.${module}`)}</p>
        ) : (
          <p className="max-w-prose text-sm text-muted-foreground">{t("notice.failedBody")}</p>
        )}
      </div>
    </div>
  );
}
