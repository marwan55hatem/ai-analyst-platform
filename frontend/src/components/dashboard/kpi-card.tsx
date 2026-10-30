"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useTranslations } from "next-intl";
import { memo } from "react";

import { Sparkline } from "@/components/charts/sparkline";
import { EngineText } from "@/components/insights/badges";
import type { Kpi } from "@/lib/api/types";
import { direction, formatChange, formatValue, isGood } from "@/lib/charts/kpi";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICON = { up: ArrowUpRight, down: ArrowDownRight, flat: Minus, none: Minus };

/**
 * One headline figure: the value, its change vs the window before the range (arrow plus words,
 * never colour alone) and a sparkline. When there is nothing to compare with, it says so.
 */
export const KpiCard = memo(function KpiCard({
  kpi,
  spark,
  previousLabel,
}: {
  kpi: Kpi;
  spark: number[];
  previousLabel: string;
}) {
  const t = useTranslations("dashboard");
  const fmt = useFormat();
  const dir = direction(kpi);
  const good = isGood(kpi.key, dir);
  const change = formatChange(kpi, fmt);
  const Icon = ICON[dir];
  const tone = good === null ? "text-muted-foreground" : good ? "text-good" : "text-bad";
  return (
    <article
      className="flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-4"
      data-testid="kpi-card"
      data-kpi={kpi.key}
      aria-labelledby={`kpi-${kpi.key}`}
    >
      <h3 id={`kpi-${kpi.key}`} className="text-sm text-muted-foreground">
        {t(`kpi.${kpi.key}`)}
      </h3>
      {kpi.available ? (
        <>
          <p className="text-2xl font-semibold tabular-nums" data-testid="kpi-value">
            {formatValue(kpi, fmt)}
          </p>
          {change ? (
            <p className={cn("flex items-center gap-1 text-sm", tone)} data-direction={dir}>
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              <span className="tabular-nums">
                {kpi.change_unit === "points" ? t("points", { value: change }) : change}
              </span>
              <span className="text-muted-foreground">{t(`direction.${dir}`, { previous: previousLabel })}</span>
            </p>
          ) : (
            <p className="text-sm text-muted-foreground" data-direction="none">
              {t("noComparison")}
            </p>
          )}
          <Sparkline values={spark} className="mt-auto h-8 w-full" />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          <EngineText>{kpi.reason ?? t("unavailable")}</EngineText>
        </p>
      )}
    </article>
  );
});
