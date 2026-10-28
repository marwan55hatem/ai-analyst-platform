"use client";

import { CheckCircle2, Circle, CircleSlash, Loader2, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Progress } from "@/components/ui/progress";
import { MODULES, isRunActive, type AnalysisRun, type ModuleState } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

export type RowState = "waiting" | "running" | "completed" | "skipped" | "failed";

/** Per-module state; the first module without a result is the one running now. */
export function moduleRows(run: AnalysisRun): { name: string; state: RowState; detail: ModuleState | null }[] {
  const done = run.progress.modules ?? {};
  const names: string[] = [...MODULES, ...Object.keys(done).filter((n) => !(MODULES as readonly string[]).includes(n))];
  const active = isRunActive(run.status);
  let runningAssigned = run.status !== "running";
  return names.map((name) => {
    const detail = done[name] ?? null;
    if (detail) return { name, state: detail.status, detail };
    if (active && !runningAssigned) {
      runningAssigned = true;
      return { name, state: "running", detail: null };
    }
    return { name, state: "waiting", detail: null };
  });
}

const ICON = {
  waiting: Circle,
  running: Loader2,
  completed: CheckCircle2,
  skipped: CircleSlash,
  failed: XCircle,
};
const TONE: Record<RowState, string> = {
  waiting: "text-muted-foreground",
  running: "text-primary",
  completed: "text-good",
  skipped: "text-warn",
  failed: "text-bad",
};

/** Live progress of one analysis run, with the backend's reason for any skip or failure. */
export function AnalysisProgress({ run }: { run: AnalysisRun }) {
  const t = useTranslations("analysis");
  const fmt = useFormat();
  const rows = moduleRows(run);
  const total = run.progress.total ?? MODULES.length;
  const done = run.progress.done ?? 0;
  const active = isRunActive(run.status);

  return (
    <div className="space-y-4" data-testid="analysis-progress" data-status={run.status}>
      <div className="space-y-2" aria-live="polite">
        <p className="font-medium">
          {active
            ? run.status === "pending"
              ? t("queued")
              : t("running", { done, total })
            : t(`result.${run.status}`)}
          {!active && run.finished_at ? (
            <span className="font-normal text-muted-foreground"> · {t("finished", { when: fmt.ago(run.finished_at) })}</span>
          ) : null}
        </p>
        <Progress value={total ? Math.round((done / total) * 100) : 0} aria-label={t("run")} />
      </div>
      <ol className="divide-y rounded-lg border bg-card">
        {rows.map(({ name, state, detail }) => {
          const Icon = ICON[state];
          const reason =
            detail?.status === "skipped"
              ? detail.reason
              : detail?.status === "failed"
                ? (detail.warnings[0] ?? detail.error)
                : null;
          return (
            <li key={name} className="flex items-start gap-3 px-4 py-3" data-module={name} data-state={state}>
              <Icon
                className={cn("mt-0.5 size-5 shrink-0", TONE[state], state === "running" && "animate-spin motion-reduce:animate-none")}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="font-medium">{t.has(`modules.${name}`) ? t(`modules.${name}`) : name}</span>
                  <span className={cn("text-sm", TONE[state])}>
                    {t(`states.${state}`)}
                    {detail?.status === "completed" ? ` · ${t("insights", { count: detail.insights })}` : null}
                  </span>
                </div>
                {reason ? (
                  <p className="mt-0.5 text-sm text-muted-foreground break-words">
                    <span lang="en" dir="ltr">
                      {reason}
                    </span>
                  </p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
