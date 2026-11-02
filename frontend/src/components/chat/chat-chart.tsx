"use client";

import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { BarChart } from "@/components/charts/bar-chart";
import { ChartFrame, DataTable } from "@/components/charts/chart-frame";
import { LineBandChart } from "@/components/charts/line-band-chart";
import type { ChartSpec } from "@/lib/chat/events";
import { toChartView } from "@/lib/chat/chart-spec";
import { buildTrend } from "@/lib/charts/trend";
import { useFormat } from "@/lib/format";

/** A chart the analyst made, on the same wrappers and with the same table view as the dashboard. */
export function ChatChart({ spec }: { spec: ChartSpec }) {
  const t = useTranslations("chat.chart");
  const tc = useTranslations("charts");
  const fmt = useFormat();
  const view = useMemo(() => toChartView(spec), [spec]);
  const labels = useMemo(
    () => ({
      actual: view.kind === "line" ? view.series : "",
      forecast: tc("forecast"),
      band80: tc("band80"),
      band95: tc("band95"),
      spike: tc("spike"),
      drop: tc("drop"),
      forecastStarts: tc("forecastStarts"),
      partial: tc("partial"),
      tickSpike: tc("tickSpike"),
      tickDrop: tc("tickDrop"),
    }),
    [tc, view],
  );
  const notes = [
    view.dropped.length && view.kind !== "table" && view.kind !== "kpi"
      ? t("moreSeries", { series: view.series, others: view.dropped.join(", ") })
      : null,
    view.truncated ? t("cut") : null,
  ].filter(Boolean);
  const monthly = view.kind === "line" && view.points.every((p) => p.period.slice(8, 10) === "01");
  return (
    <ChartFrame
      testId="chat-chart"
      title={spec.title}
      table={view.kind === "table" ? undefined : view.table}
      footer={notes.length ? notes.join(" ") : undefined}
    >
      {view.kind === "kpi" ? (
        <p>
          <span className="block text-sm text-muted-foreground" dir="auto">
            {view.label}
          </span>
          <span className="text-3xl font-semibold tabular-nums">
            {typeof view.value === "number"
              ? Number.isInteger(view.value)
                ? fmt.int(view.value)
                : fmt.dec2(view.value)
              : String(view.value ?? "—")}
          </span>
        </p>
      ) : null}
      {view.kind === "line" ? (
        <LineBandChart
          points={buildTrend(view.points, [], [])}
          labels={labels}
          formatValue={fmt.money}
          formatTick={fmt.compact}
          formatPeriod={monthly ? fmt.month : fmt.day}
          ariaLabel={t("lineAria", { title: spec.title })}
        />
      ) : null}
      {view.kind === "bar" ? (
        <BarChart data={view.bars} formatValue={fmt.compact} ariaLabel={t("barAria", { title: spec.title })} />
      ) : null}
      {view.kind === "table" ? <DataTable table={view.table} caption={spec.title} /> : null}
    </ChartFrame>
  );
}
