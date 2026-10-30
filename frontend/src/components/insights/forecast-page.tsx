"use client";

import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { ChartFrame } from "@/components/charts/chart-frame";
import { LineBandChart } from "@/components/charts/line-band-chart";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveDataset } from "@/lib/active-dataset";
import type { AnalysisRun, ForecastScore, ForecastSpec, Insight } from "@/lib/api/types";
import { buildTrend } from "@/lib/charts/trend";
import { useFormat } from "@/lib/format";
import { useForecasts } from "@/lib/queries";

import { AnalysisGate, DatasetPicker, ModuleNotice } from "./analysis-gate";
import { ConfidenceBadge, EngineText, FactBadge } from "./badges";

/** /insights/forecast: history, forecast with 80/95% ranges, and how well the model tested. */
export function ForecastPage() {
  const t = useTranslations("forecast");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<DatasetPicker active={active} />} />
      <AnalysisGate active={active}>
        {(run) =>
          active.dataset ? <ForecastView run={run} datasetId={active.dataset.id} version={active.version} /> : null
        }
      </AnalysisGate>
    </>
  );
}

/** Name of a forecast series: "Total" or the group (category) it covers. */
function seriesName(insight: Insight): string {
  return String(insight.payload.series ?? insight.payload.group ?? insight.title);
}

/** The backtest in plain words: how much smaller the model's errors were than the baseline's. */
export function backtestSummary(payload: Insight["payload"]): {
  windows: number;
  horizon: number;
  improvement: number | null;
  winner: ForecastScore | null;
  baseline: ForecastScore | null;
  beatBaseline: boolean;
} {
  const winner = (payload.winner as ForecastScore | undefined) ?? null;
  const baseline = (payload.baseline as ForecastScore | undefined) ?? null;
  const backtest = (payload.backtest as { windows?: number; horizon?: number } | undefined) ?? {};
  const improvement =
    winner?.mase != null && baseline?.mase ? 100 * (1 - winner.mase / baseline.mase) : null;
  return {
    windows: backtest.windows ?? 0,
    horizon: backtest.horizon ?? 0,
    improvement,
    winner,
    baseline,
    beatBaseline: !payload.no_better_than_baseline && (improvement ?? 0) > 0,
  };
}

function ForecastView({ run, datasetId, version }: { run: AnalysisRun; datasetId: string; version: number }) {
  const t = useTranslations("forecast");
  const tc = useTranslations("charts");
  const fmt = useFormat();
  const found = useForecasts(run.id);
  const forecasts = useMemo(() => found.data?.forecasts ?? [], [found.data]);
  const [selected, setSelected] = useState<string | null>(null);
  const current = forecasts.find((f) => f.id === selected) ?? forecasts.find((f) => f.type === "forecast_total") ?? forecasts[0];
  const spec = current?.chart_spec as unknown as ForecastSpec | undefined;
  const points = useMemo(
    () => (spec ? buildTrend(spec.history.map((h) => ({ period: h.period, value: h.value })), spec.forecast) : []),
    [spec],
  );
  const labels = useMemo(
    () => ({
      actual: tc("actual"),
      forecast: tc("forecast"),
      band80: tc("band80"),
      band95: tc("band95"),
      spike: tc("spike"),
      drop: tc("drop"),
      forecastStarts: tc("forecastStarts"),
      partial: tc("partial"),
    }),
    [tc],
  );

  if (run.progress.modules?.forecast?.status !== "completed") {
    return <ModuleNotice run={run} module="forecast" datasetId={datasetId} version={version} />;
  }
  if (found.isPending) return <Skeleton className="h-96 w-full" />;
  if (found.error) return <ErrorState title={t("loadFailed")} error={found.error} onRetry={() => void found.refetch()} />;
  if (!current || !spec) {
    return (
      <p className="text-muted-foreground">
        {found.data?.notes?.length ? <EngineText>{found.data.notes.join(" ")}</EngineText> : t("none")}
      </p>
    );
  }

  const bt = backtestSummary(current.payload);
  const unit = t(`unit.${spec.unit}`);
  const periodFormat = spec.unit === "month" || spec.unit === "quarter" ? fmt.month : fmt.shortDay;
  const next = spec.forecast[0];
  const model = String(current.payload.model ?? bt.winner?.model ?? "");
  return (
    <div className="space-y-6">
      {forecasts.length > 1 ? (
        <div className="flex flex-col gap-1">
          <Label htmlFor="forecast-series" className="text-xs text-muted-foreground">
            {t("series")}
          </Label>
          <Select value={current.id} onValueChange={setSelected}>
            <SelectTrigger id="forecast-series" className="w-64 max-w-full" data-testid="forecast-series">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {forecasts.map((f) => (
                <SelectItem key={f.id} value={f.id}>
                  <bdi>{f.type === "forecast_total" ? t("total") : seriesName(f)}</bdi>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      {next ? (
        <section className="rounded-lg border bg-card p-5" aria-labelledby="forecast-answer" data-testid="forecast-answer">
          <h2 id="forecast-answer" className="text-sm text-muted-foreground">
            {t("nextPeriod", { period: periodFormat(next.period) })}
          </h2>
          <p className="mt-1 text-3xl font-semibold tabular-nums">{fmt.money(next.point)}</p>
          <p className="mt-1 text-sm">
            {t("range80", { low: fmt.money(next.lo80), high: fmt.money(next.hi80) })}
            <span className="text-muted-foreground"> · {t("range95", { low: fmt.money(next.lo95), high: fmt.money(next.hi95) })}</span>
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <ConfidenceBadge level={current.confidence} sampleSize={current.sample_size} />
            <FactBadge>{t("model", { model })}</FactBadge>
          </div>
        </section>
      ) : null}

      <ChartFrame
        testId="forecast-chart"
        title={<EngineText>{spec.title}</EngineText>}
        takeaway={t("takeaway", { periods: spec.forecast.length, unit })}
        unit={t("chartUnit", { unit })}
        table={{
          columns: [tc("period"), tc("actual"), tc("forecast"), tc("low95"), tc("low80"), tc("high80"), tc("high95")],
          numeric: [1, 2, 3, 4, 5, 6],
          rows: [
            ...spec.history.map((h) => [periodFormat(h.period), fmt.money(h.value), "—", "—", "—", "—", "—"]),
            ...spec.forecast.map((f) => [
              periodFormat(f.period),
              "—",
              fmt.money(f.point),
              fmt.money(f.lo95),
              fmt.money(f.lo80),
              fmt.money(f.hi80),
              fmt.money(f.hi95),
            ]),
          ],
        }}
      >
        <LineBandChart
          points={points}
          labels={labels}
          formatValue={fmt.money}
          formatTick={fmt.compact}
          formatPeriod={periodFormat}
          height={320}
          ariaLabel={t("aria")}
        />
      </ChartFrame>

      <section className="space-y-3 rounded-lg border bg-card p-5" aria-labelledby="backtest" data-testid="backtest">
        <h2 id="backtest" className="text-lg font-semibold">
          {t("backtest.title")}
        </h2>
        <p className="max-w-prose">
          {bt.beatBaseline && bt.improvement !== null
            ? t("backtest.better", {
                windows: bt.windows,
                horizon: bt.horizon,
                unit,
                model,
                pct: fmt.dec(bt.improvement),
              })
            : t("backtest.notBetter", { windows: bt.windows, horizon: bt.horizon, unit, model })}
        </p>
        {bt.winner?.coverage80 != null ? (
          <p className="max-w-prose text-sm text-muted-foreground">
            {t("backtest.coverage", { pct: fmt.pct(bt.winner.coverage80) })}
          </p>
        ) : null}
        {!bt.beatBaseline ? (
          <p className="rounded-md bg-warn-soft p-3 text-sm text-warn" role="note">
            {t("backtest.lowConfidence")}
          </p>
        ) : null}
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">{t("backtest.mase", { model })}</dt>
            <dd className="tabular-nums">{bt.winner?.mase != null ? fmt.dec2(bt.winner.mase) : "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("backtest.baselineMase")}</dt>
            <dd className="tabular-nums">{bt.baseline?.mase != null ? fmt.dec2(bt.baseline.mase) : "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("backtest.smape")}</dt>
            <dd className="tabular-nums">{bt.winner?.smape != null ? `${fmt.dec(bt.winner.smape)}%` : "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("backtest.windows")}</dt>
            <dd className="tabular-nums">{fmt.int(bt.windows)}</dd>
          </div>
        </dl>
        <p className="text-xs text-muted-foreground">{t("backtest.maseHelp")}</p>
        <p className="text-sm">
          <EngineText>{current.summary}</EngineText>
        </p>
      </section>
    </div>
  );
}
