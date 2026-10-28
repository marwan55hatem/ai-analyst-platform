"use client";

import { ArrowUpRight, CheckCircle2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { DIMENSIONS, type Quality } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

import { bandOf } from "./quality-bar";
import { QualityDial } from "./quality-dial";

const BAND_TEXT = { good: "text-good", fair: "text-warn", poor: "text-bad" } as const;

/** Dial, band, change since the source version and the five-part breakdown. */
export function HealthPanel({ quality, previous }: { quality: Quality; previous: number | null }) {
  const t = useTranslations("health");
  const tc = useTranslations("common");
  const fmt = useFormat();
  const band = bandOf(quality.score);
  const delta = previous === null ? null : quality.score - previous;

  return (
    <div className="grid gap-6 sm:grid-cols-[minmax(0,15rem)_1fr] sm:items-center">
      <div className="space-y-2 text-center">
        <QualityDial score={quality.score} previous={previous} />
        <p className={cn("font-medium", BAND_TEXT[band])}>{t(`bands.${band}`)}</p>
        {delta !== null && previous !== null ? (
          <p className="text-sm text-muted-foreground" data-testid="score-change">
            <span className="inline-flex items-center gap-1 font-medium text-foreground tabular-nums">
              {delta > 0 ? <ArrowUpRight className="size-4 text-good rtl:-scale-x-100" aria-hidden="true" /> : null}
              {t("change", { delta: Math.abs(delta), sign: delta > 0 ? "+" : delta < 0 ? "−" : "" })}
            </span>{" "}
            · {t("previous", { score: previous })}
          </p>
        ) : null}
      </div>
      <div className="space-y-4">
        <dl className="space-y-3">
          {DIMENSIONS.map((dim) => {
            const score = quality.breakdown[dim] ?? 0;
            const max = quality.max[dim] ?? 0;
            const ratio = max > 0 ? score / max : 0;
            return (
              <div key={dim} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-3 text-sm">
                <dt>{t(`dimensions.${dim}`)}</dt>
                <dd aria-hidden="true" className="h-2 overflow-hidden rounded-full bg-dial-track">
                  <span
                    className={cn(
                      "block h-full rounded-full",
                      ratio >= 0.85 ? "bg-good" : ratio >= 0.6 ? "bg-warn" : "bg-bad",
                    )}
                    style={{ width: `${Math.max(2, ratio * 100)}%` }}
                  />
                </dd>
                <dd className="text-end text-muted-foreground tabular-nums">
                  {t("dimensionValue", { score: fmt.dec(score), max: fmt.int(max) })}
                </dd>
              </div>
            );
          })}
        </dl>
        <p className="text-xs text-muted-foreground">
          {tc("sampleSize", { count: quality.sample_size })} · {tc(`confidence.${quality.confidence}`)}.{" "}
          {t("relative")}
        </p>
        {quality.warnings.length ? (
          <ul className="space-y-1 text-sm text-warn">
            {quality.warnings.map((w) => (
              <li key={w}>
                <span lang="en" dir="ltr">
                  {w}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

/** The costliest issues in plain language (text from the backend, English). */
export function IssueList({ quality }: { quality: Quality }) {
  const t = useTranslations("health");
  const tc = useTranslations("common");
  const fmt = useFormat();
  if (!quality.issues.length) {
    return (
      <p className="flex items-center gap-2 text-good">
        <CheckCircle2 className="size-5" aria-hidden="true" />
        {t("noIssues")}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <ol className="divide-y rounded-lg border bg-card" data-testid="issues">
        {quality.issues.map((issue) => (
          <li key={`${issue.code}-${issue.columns.join(",")}`} className="grid gap-1 p-4 sm:grid-cols-[1fr_auto] sm:gap-4">
            <div className="min-w-0 space-y-1">
              <p className="font-medium break-words">
                <span lang="en" dir="ltr">
                  {issue.title}
                </span>
              </p>
              <p className="text-sm text-muted-foreground break-words">
                <span lang="en" dir="ltr">
                  {issue.detail}
                </span>
              </p>
              <p className="text-sm break-words">
                <span className="text-muted-foreground">{t("suggestion")}: </span>
                <span lang="en" dir="ltr">
                  {issue.suggested_fix}
                </span>
              </p>
            </div>
            <p className="text-sm font-medium text-bad tabular-nums sm:text-end">
              {t("pointsLost", { points: fmt.dec(issue.points_lost) })}
            </p>
          </li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">{tc("fromServerEnglish")}</p>
    </div>
  );
}
