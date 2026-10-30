"use client";

import { ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";

import type { InsightSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils";

import { ConfidenceBadge, EngineText, SeverityBadge } from "./badges";

/** A ranked list of insights; each row opens the evidence drawer. */
export function InsightList({
  insights,
  onOpen,
  ranked = true,
  compact = false,
}: {
  insights: InsightSummary[];
  onOpen: (id: string) => void;
  ranked?: boolean;
  compact?: boolean;
}) {
  const t = useTranslations("insights");
  const List = ranked ? "ol" : "ul";
  return (
    <List className="divide-y rounded-lg border bg-card" data-testid="insight-list">
      {insights.map((insight, index) => (
        <li key={insight.id}>
          <button
            type="button"
            onClick={() => onOpen(insight.id)}
            className="flex w-full items-start gap-3 px-4 py-3 text-start hover:bg-accent focus-visible:bg-accent"
            data-insight={insight.id}
            data-module={insight.module}
          >
            {ranked ? (
              <span className="w-6 shrink-0 pt-0.5 text-sm text-muted-foreground tabular-nums" aria-hidden="true">
                {index + 1}
              </span>
            ) : null}
            <span className="min-w-0 flex-1 space-y-1.5">
              <span className="block font-medium">
                <EngineText>{insight.title}</EngineText>
              </span>
              {!compact ? (
                <span className="line-clamp-2 text-sm text-muted-foreground">
                  <EngineText>{insight.summary}</EngineText>
                </span>
              ) : null}
              <span className="flex flex-wrap items-center gap-2">
                <SeverityBadge impact={insight.impact_score} />
                <ConfidenceBadge level={insight.confidence} sampleSize={insight.sample_size} />
                <span className="text-xs text-muted-foreground">
                  {t.has(`modules.${insight.module}`) ? t(`modules.${insight.module}`) : insight.module}
                </span>
              </span>
            </span>
            <ChevronRight className={cn("mt-1 size-4 shrink-0 text-muted-foreground rtl:-scale-x-100")} aria-hidden="true" />
            <span className="sr-only">{t("openEvidence")}</span>
          </button>
        </li>
      ))}
    </List>
  );
}
