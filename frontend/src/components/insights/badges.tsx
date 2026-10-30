"use client";

import { Gauge, Info, SignalHigh, SignalLow, SignalMedium, Zap } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import type { Confidence } from "@/lib/api/types";
import { severity } from "@/lib/charts/range";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

const CONFIDENCE_STYLE: Record<Confidence, string> = {
  high: "bg-good-soft text-good",
  medium: "bg-info-soft text-primary",
  low: "bg-warn-soft text-warn",
};
const CONFIDENCE_ICON = { high: SignalHigh, medium: SignalMedium, low: SignalLow };

/** Confidence as an icon and words, with the sample size behind it (golden rule 3). */
export function ConfidenceBadge({ level, sampleSize }: { level: Confidence; sampleSize?: number }) {
  const t = useTranslations("insights");
  const fmt = useFormat();
  const Icon = CONFIDENCE_ICON[level];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        CONFIDENCE_STYLE[level],
      )}
      data-confidence={level}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {t(`confidence.${level}`)}
      {sampleSize !== undefined ? (
        <span className="font-normal">· {t("sample", { n: fmt.int(sampleSize) })}</span>
      ) : null}
    </span>
  );
}

// Impact is not good or bad news, so it never borrows the risk colours: an outlined chip whose
// weight and icon rise with the score.
const SEVERITY_STYLE = {
  high: "border-primary font-semibold text-primary",
  medium: "border-input text-foreground",
  low: "border-border text-muted-foreground",
};
const SEVERITY_ICON = { high: Zap, medium: Gauge, low: Info };

/** How much an insight matters, from its 0-100 impact score. */
export function SeverityBadge({ impact }: { impact: number }) {
  const t = useTranslations("insights");
  const level = severity(impact);
  const Icon = SEVERITY_ICON[level];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        SEVERITY_STYLE[level],
      )}
      data-severity={level}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {t(`severity.${level}`)}
    </span>
  );
}

/** A neutral chip, e.g. the model that produced a forecast. */
export function FactBadge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs whitespace-nowrap">
      <Info className="size-3.5 text-muted-foreground" aria-hidden="true" />
      {children}
    </span>
  );
}

/**
 * Text written by the analysis engine (English for now). It keeps its own language and
 * direction inside Arabic pages, as docs/design.md requires.
 */
export function EngineText({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span lang="en" dir="ltr" className={className}>
      {children}
    </span>
  );
}
