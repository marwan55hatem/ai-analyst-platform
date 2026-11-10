"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BellRing,
  CheckCircle2,
  Info,
  Loader2,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { withDataset } from "@/lib/active-dataset";
import {
  isRunActive,
  type DatasetDetail,
  type VersionCompatibility,
  type VersionSummary,
} from "@/lib/api/types";
import {
  keys,
  useLatestRun,
  useVersionCompatibility,
  useVersions,
} from "@/lib/queries";

import { AnalysisProgress } from "./analysis-progress";
import { NewVersionDialog } from "./new-version-dialog";

/** How long to wait for the automatic analysis to appear after the profile is ready. */
const RUN_GRACE_MS = 20_000;
/** The alert check runs right after the analysis; give it a moment before saying "no alerts". */
const ALERT_SETTLE_MS = 2_500;

type Stage =
  | "reading"
  | "checking"
  | "waiting"
  | "analysing"
  | "finishing"
  | "done"
  | "blocked";

function readStage(args: {
  summary: VersionSummary | undefined;
  compat: VersionCompatibility | undefined;
  analyze: boolean;
  runActive: boolean;
  hasRun: boolean;
  runFinished: boolean;
  graceOver: boolean;
  settled: boolean;
}): Stage {
  const { summary, compat, analyze } = args;
  if (summary?.status === "failed" || compat?.status === "blocked")
    return "blocked";
  if (
    !summary ||
    summary.status === "uploaded" ||
    summary.status === "processing"
  )
    return "reading";
  if (summary.profile_status === "failed") return "blocked";
  if (
    summary.profile_status !== "ready" ||
    !compat ||
    compat.status === "pending"
  )
    return "checking";
  if (!analyze) return "done";
  if (args.runActive) return "analysing";
  if (!args.hasRun) return args.graceOver ? "done" : "waiting";
  if (args.runFinished && !args.settled) return "finishing";
  return "done";
}

function names(list: string[]): string {
  return list.join(", ");
}

function Compatibility({ compat }: { compat: VersionCompatibility }) {
  const t = useTranslations("versions.compat");
  if (compat.status === "pending") return null;
  if (compat.previous_version === null) {
    return compat.status === "initial" ? (
      <p className="text-sm">{t("initial")}</p>
    ) : null;
  }
  const lines: ReactNode[] = [];
  if (compat.added.length)
    lines.push(
      <li key="added">
        {t("added", { count: compat.added.length, names: names(compat.added) })}
      </li>,
    );
  if (compat.missing.length)
    lines.push(
      <li key="missing">
        {t("missing", {
          count: compat.missing.length,
          names: names(compat.missing),
        })}
      </li>,
    );
  if (compat.changed.length)
    lines.push(
      <li key="changed">
        {t("changed", { count: compat.changed.length })}
        <ul className="mt-1 list-disc space-y-0.5 ps-5">
          {compat.changed.map((c) => (
            <li key={c.name}>
              <span dir="auto">
                {t("changedItem", {
                  name: c.name,
                  from:
                    c.previous_type === c.new_type
                      ? c.previous_role
                      : c.previous_type,
                  to: c.previous_type === c.new_type ? c.new_role : c.new_type,
                })}
              </span>
            </li>
          ))}
        </ul>
      </li>,
    );
  if (compat.missing_roles.length)
    lines.push(
      <li key="roles">
        {t("lostRoles", {
          roles: compat.missing_roles
            .map((r) => (r === "date" ? t("roleDate") : t("roleRevenue")))
            .join(" / "),
        })}
      </li>,
    );
  return (
    <div
      className="space-y-2 text-sm"
      data-testid="compat"
      data-status={compat.status}
    >
      <p className="font-medium">
        {t("title", { previous: compat.previous_version })}
      </p>
      {lines.length === 0 && !compat.shorter_period ? (
        <p>{t("same", { previous: compat.previous_version })}</p>
      ) : (
        <ul className="list-disc space-y-1 ps-5">{lines}</ul>
      )}
      {compat.shorter_period ? (
        <p
          role="note"
          className="flex gap-2 rounded-md border border-signal/50 bg-warn-soft p-3"
          data-testid="compat-shorter"
        >
          <AlertTriangle
            className="mt-0.5 size-4 shrink-0 text-signal"
            aria-hidden="true"
          />
          <span>{t("shorter")}</span>
        </p>
      ) : null}
    </div>
  );
}

/** What happens after "Upload new data": compatibility, progress, and a clear outcome. */
export function VersionRefreshPanel({
  datasetId,
  version,
  analyze,
  onDismiss,
  onUploaded,
}: {
  datasetId: string;
  version: number;
  analyze: boolean;
  onDismiss: () => void;
  onUploaded: (detail: DatasetDetail, analyze: boolean) => void;
}) {
  const t = useTranslations("versions");
  const client = useQueryClient();
  const compat = useVersionCompatibility(datasetId, version);
  const versions = useVersions(datasetId);
  const summary = versions.data?.find((v) => v.version === version);
  const run = useLatestRun(
    datasetId,
    version,
    analyze && summary?.profile_status === "ready",
  );
  const [graceOver, setGraceOver] = useState(false);
  const [settled, setSettled] = useState(false);

  const profileReady = summary?.profile_status === "ready";
  const runFinished = !!run.data && !isRunActive(run.data.status);

  useEffect(() => {
    if (!profileReady || !analyze) return;
    const timer = setTimeout(() => setGraceOver(true), RUN_GRACE_MS);
    return () => clearTimeout(timer);
  }, [profileReady, analyze]);

  useEffect(() => {
    if (!runFinished) return;
    const timer = setTimeout(() => {
      setSettled(true);
      void client.invalidateQueries({ queryKey: keys.versions(datasetId) });
      void client.invalidateQueries({ queryKey: keys.alerts });
    }, ALERT_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [runFinished, client, datasetId]);

  const stage = readStage({
    summary,
    compat: compat.data,
    analyze,
    runActive: isRunActive(run.data?.status),
    hasRun: !!run.data,
    runFinished,
    graceOver,
    settled,
  });
  const alerts = summary?.alerts_raised ?? 0;
  const failedRun = run.data?.status === "failed";
  const finished = stage === "done" || stage === "blocked";
  const stepKey = stage === "waiting" ? "analysing" : stage;

  return (
    <section
      aria-labelledby="refresh-h"
      className="space-y-4 rounded-lg border bg-card p-5"
      data-testid="refresh-panel"
      data-stage={stage}
    >
      <div className="flex items-start justify-between gap-3">
        <h2
          id="refresh-h"
          className="flex items-center gap-2 text-lg font-semibold"
        >
          {stage === "blocked" ? (
            <XCircle className="size-5 text-bad" aria-hidden="true" />
          ) : finished ? (
            <CheckCircle2 className="size-5 text-good" aria-hidden="true" />
          ) : (
            <Loader2
              className="size-5 animate-spin text-primary motion-reduce:animate-none"
              aria-hidden="true"
            />
          )}
          {stage === "done" && !failedRun
            ? t("outcome.ready", { version })
            : stage === "blocked"
              ? t("compat.blockedTitle")
              : t("panelTitle", { version })}
        </h2>
        {finished ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onDismiss}
            data-testid="refresh-dismiss"
          >
            {t("dismiss")}
          </Button>
        ) : null}
      </div>

      <div role="status" aria-live="polite" className="text-sm">
        {!finished ? (
          <p data-testid="refresh-step">
            {stage === "waiting"
              ? t("outcome.waiting")
              : t(`steps.${stepKey}` as "steps.reading")}
          </p>
        ) : null}
      </div>

      {compat.data && stage !== "reading" ? (
        <Compatibility compat={compat.data} />
      ) : null}

      {stage === "blocked" ? (
        <div className="space-y-3" data-testid="refresh-blocked">
          {compat.data?.blocking_reason ? (
            <p className="text-sm">
              <span lang="en" dir="ltr">
                {compat.data.blocking_reason}
              </span>
            </p>
          ) : null}
          <p className="text-sm">{t("compat.blockedAction")}</p>
          <NewVersionDialog datasetId={datasetId} onUploaded={onUploaded} />
        </div>
      ) : null}

      {analyze &&
      run.data &&
      stage !== "blocked" &&
      stage !== "reading" &&
      stage !== "checking" ? (
        <AnalysisProgress run={run.data} />
      ) : null}

      {stage === "done" ? (
        <div className="space-y-3" data-testid="refresh-outcome">
          {!analyze ? (
            <p className="text-sm">
              {t("outcome.readyNoAnalysis", { version })}
            </p>
          ) : null}
          {analyze && failedRun ? (
            <p className="text-sm">
              {t("outcome.analysisFailed", { version })}
            </p>
          ) : null}
          {analyze && !failedRun && !run.data ? (
            <p className="text-sm">
              {t("outcome.readyNoAnalysis", { version })}
            </p>
          ) : null}
          {analyze && !!run.data && !failedRun ? (
            alerts > 0 ? (
              <p
                className="flex items-center gap-2 text-sm font-medium"
                data-testid="refresh-alerts"
              >
                <BellRing className="size-4 text-signal" aria-hidden="true" />
                <Link
                  href="/reports?tab=alerts"
                  className="underline underline-offset-4"
                  data-testid="refresh-alerts-link"
                >
                  {t("outcome.alerts", { count: alerts })}
                </Link>
              </p>
            ) : (
              <p
                className="flex items-center gap-2 text-sm"
                data-testid="refresh-no-alerts"
              >
                <Info
                  className="size-4 text-muted-foreground"
                  aria-hidden="true"
                />
                {t("outcome.noAlerts")}
              </p>
            )
          ) : null}
          <Button asChild>
            <Link
              href={withDataset("/dashboard", datasetId)}
              data-testid="refresh-dashboard"
            >
              {t("outcome.dashboard")}
            </Link>
          </Button>
        </div>
      ) : null}
    </section>
  );
}
