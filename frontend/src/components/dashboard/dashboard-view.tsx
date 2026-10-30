"use client";

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import { BarChart, PatternSwatch } from "@/components/charts/bar-chart";
import { ChartFrame } from "@/components/charts/chart-frame";
import { LineBandChart } from "@/components/charts/line-band-chart";
import { ErrorState } from "@/components/common/error-state";
import { ConfidenceBadge, EngineText } from "@/components/insights/badges";
import { InsightDrawer } from "@/components/insights/insight-drawer";
import { InsightList } from "@/components/insights/insight-list";
import { ModuleNotice } from "@/components/insights/analysis-gate";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { withDataset } from "@/lib/active-dataset";
import type { AnalysisRun, AnomalyEvent, ForecastSpec, Grain, KpiKey, SeriesPoint } from "@/lib/api/types";
import { sparkValues } from "@/lib/charts/kpi";
import { byDimension, DIMENSIONS, itemsOf, paretoBars, type Dimension } from "@/lib/charts/pareto";
import {
  autoGrain,
  grainAllowed,
  presetRange,
  PRESETS,
  validDay,
  type DateRange,
  type Preset,
} from "@/lib/charts/range";
import { buildTrend } from "@/lib/charts/trend";
import { useFormat } from "@/lib/format";
import { useUrlUpdate } from "@/lib/url-state";
import { useAnomalies, useForecasts, useInsights, useModuleInsights, useTimeseries } from "@/lib/queries";

import { KpiCard } from "./kpi-card";
import { RangeControls } from "./range-controls";

const KPI_ORDER: KpiKey[] = ["net_revenue", "orders", "aov", "units", "margin_pct"];
const GRAINS: Grain[] = ["day", "week", "month"];

/**
 * Read the range, preset and grain from the URL. The default is the last 90 days: short enough
 * that the window before it is usually inside the data, so every KPI has a comparison.
 */
function useRangeState(data: DateRange | null) {
  const params = useSearchParams();
  const presetParam = params.get("range");
  const preset: Preset | "custom" =
    presetParam === "custom" || (PRESETS as readonly string[]).includes(presetParam ?? "")
      ? (presetParam as Preset | "custom")
      : "90d";
  let range: DateRange | null = null;
  if (data) {
    if (preset === "custom") {
      const start = validDay(params.get("start"), data) ?? data.start;
      const end = validDay(params.get("end"), data) ?? data.end;
      range = start <= end ? { start, end } : presetRange("all", data);
    } else {
      range = presetRange(preset, data);
    }
  }
  const grainParam = params.get("grain") as Grain | null;
  const grain: Grain =
    range && grainParam && GRAINS.includes(grainParam) && grainAllowed(range, grainParam)
      ? grainParam
      : range
        ? autoGrain(range)
        : "month";

  const update = useUrlUpdate();
  return { preset, range, grain, update };
}

/** The dashboard of one analysed dataset: answer first (KPIs), then the evidence charts. */
export function DashboardView({ run, datasetId, version }: { run: AnalysisRun; datasetId: string; version: number }) {
  const t = useTranslations("dashboard");
  const tc = useTranslations("charts");
  const fmt = useFormat();
  const [openInsight, setOpenInsight] = useState<string | null>(null);

  // The whole data span by month: cheap, cached, and it tells the presets where the data ends.
  const bounds = useTimeseries(datasetId, version, { grain: "month" }, true);
  const data = bounds.data?.data_range ?? null;
  const { preset, range, grain, update } = useRangeState(data);
  const series = useTimeseries(
    datasetId,
    version,
    { start: range?.start, end: range?.end, grain },
    Boolean(range),
  );
  const forecasts = useForecasts(run.id);
  const anomalies = useAnomalies(run.id);
  const pareto = useModuleInsights(run.id, "pareto");
  const attention = useInsights(run.id);

  const total = forecasts.data?.forecasts.find((f) => f.type === "forecast_total");
  const spec = total?.chart_spec as unknown as ForecastSpec | undefined;
  const atDataEnd = Boolean(range && data && range.end === data.end);
  const showForecast = Boolean(spec && spec.unit === grain && atDataEnd);
  const events = useMemo(
    () =>
      ((anomalies.data?.events ?? []) as unknown as AnomalyEvent[])
        .filter((e) => range && e.start >= range.start && e.start <= range.end)
        .sort((a, b) => a.start.localeCompare(b.start)),
    [anomalies.data, range],
  );

  const points = useMemo(() => {
    const actual = (series.data?.series ?? []).map((p: SeriesPoint) => ({
      period: p.period,
      value: p.net_revenue,
      partial: p.partial,
    }));
    return buildTrend(actual, showForecast ? spec?.forecast : [], events, range?.end);
  }, [series.data, showForecast, spec, events, range?.end]);

  const sparks = useMemo(() => {
    const s = series.data?.series ?? [];
    return Object.fromEntries(KPI_ORDER.map((k) => [k, sparkValues(s, k)])) as Record<KpiKey, number[]>;
  }, [series.data]);

  // Timing mark for the "renders in under 2 s after data loads" check (e2e/dashboard.spec.ts).
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!series.data || series.isPlaceholderData) return;
    const id = requestAnimationFrame(() => {
      performance.mark("dashboard-ready");
      setReady(true);
    });
    return () => cancelAnimationFrame(id);
  }, [series.data, series.isPlaceholderData]);

  const dims = useMemo(() => byDimension(pareto.data ?? []), [pareto.data]);
  const comparisonGap = series.data?.kpis.find((k) => k.available && k.change === null && k.reason)?.reason ?? null;
  const top = useMemo(() => (attention.data?.pages[0] ?? []).slice(0, 5), [attention.data]);
  const periodFormat = grain === "month" ? fmt.month : fmt.shortDay;
  const previousLabel = t("previousPeriod");

  const chartLabels = useMemo(
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

  if (bounds.error) {
    return <ErrorState title={t("loadFailed")} error={bounds.error} onRetry={() => void bounds.refetch()} />;
  }

  return (
    <div className="space-y-8" data-testid="dashboard" data-ready={ready ? "true" : "false"}>
      {range && data ? (
        <RangeControls
          key={`${preset}:${range.start}:${range.end}`}
          preset={preset}
          range={range}
          data={data}
          grain={grain}
          onPreset={(p) => update({ range: p, start: null, end: null, grain: null })}
          onCustom={(r) => update({ range: "custom", start: r.start, end: r.end, grain: null })}
          onGrain={(g) => update({ grain: g })}
        />
      ) : (
        <Skeleton className="h-20 w-full" />
      )}

      <section aria-labelledby="kpi-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="kpi-heading" className="text-lg font-semibold">
            {t("kpiHeading")}
          </h2>
          {series.data ? (
            <p className="text-sm text-muted-foreground" data-testid="range-summary">
              {t("rangeSummary", {
                start: fmt.day(series.data.range.start),
                end: fmt.day(series.data.range.end),
                prevStart: fmt.day(series.data.previous_range.start),
                prevEnd: fmt.day(series.data.previous_range.end),
              })}
            </p>
          ) : null}
        </div>
        {series.error ? (
          <ErrorState title={t("loadFailed")} error={series.error} onRetry={() => void series.refetch()} />
        ) : !series.data ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {KPI_ORDER.map((k) => (
              <Skeleton key={k} className="h-36" />
            ))}
          </div>
        ) : (
          <>
            <div
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5"
              aria-busy={series.isFetching}
              data-testid="kpi-grid"
            >
              {KPI_ORDER.map((key) => {
                const kpi = series.data.kpis.find((k) => k.key === key);
                return kpi ? <KpiCard key={key} kpi={kpi} spark={sparks[key]} previousLabel={previousLabel} /> : null;
              })}
            </div>
            {comparisonGap ? (
              <p className="text-sm text-muted-foreground" data-testid="comparison-note">
                <EngineText>{comparisonGap}</EngineText>
              </p>
            ) : null}
            {series.data.warnings.length ? (
              <ul className="space-y-1 text-sm text-muted-foreground">
                {series.data.warnings.map((w) => (
                  <li key={w}>
                    <EngineText>{w}</EngineText>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </section>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <ChartFrame
          testId="trend-chart"
          title={t("trend.title")}
          takeaway={
            showForecast && spec
              ? t("trend.takeawayForecast", { periods: spec.forecast.length })
              : t("trend.takeaway", { count: events.length })
          }
          unit={t("trend.unit", { grain: t(`grain.${grain}`) })}
          badges={series.data ? <ConfidenceBadge level={series.data.confidence} sampleSize={series.data.sample_size} /> : null}
          loading={!series.data}
          table={{
            columns: [tc("period"), tc("actual"), tc("forecast"), tc("low80"), tc("high80"), tc("marker")],
            numeric: [1, 2, 3, 4],
            rows: points.map((p) => [
              periodFormat(p.period),
              p.actual === null ? "—" : fmt.money(p.actual),
              p.actual === null && p.forecast !== null ? fmt.money(p.forecast) : "—",
              p.band80 ? fmt.money(p.band80[0]) : "—",
              p.band80 ? fmt.money(p.band80[1]) : "—",
              p.marker ? tc(p.marker) : "",
            ]),
          }}
          footer={
            spec && !showForecast && data ? (
              <span className="flex flex-wrap items-center gap-2">
                {t("trend.forecastHidden", { grain: t(`grain.${spec.unit}`) })}
                {grainAllowed({ start: range?.start ?? data.start, end: data.end }, spec.unit as Grain) ? (
                  <Button
                    variant="link"
                    size="sm"
                    className="h-auto p-0"
                    onClick={() =>
                      update(
                        atDataEnd
                          ? { grain: spec.unit }
                          : { range: "all", start: null, end: null, grain: spec.unit },
                      )
                    }
                  >
                    {t("trend.showForecast")}
                  </Button>
                ) : null}
              </span>
            ) : undefined
          }
        >
          <LineBandChart
            points={points}
            labels={chartLabels}
            formatValue={fmt.money}
            formatTick={fmt.compact}
            formatPeriod={periodFormat}
            ariaLabel={t("trend.aria")}
          />
          {events.length ? (
            <div className="space-y-2 pt-2">
              <h3 className="text-sm font-medium">{t("trend.events", { count: events.length })}</h3>
              <ul className="flex flex-wrap gap-2">
                {events.slice(0, 6).map((e) => (
                  <li key={`${e.start}-${e.kind}`}>
                    {e.insight_id ? (
                      <Button variant="outline" size="sm" onClick={() => e.insight_id && setOpenInsight(e.insight_id)}>
                        <span className={e.kind === "spike" ? "text-good" : "text-bad"} aria-hidden="true">
                          {e.kind === "spike" ? "▲" : "▼"}
                        </span>
                        {fmt.day(e.start)} · {tc(e.kind)}
                      </Button>
                    ) : (
                      <span className="inline-flex h-8 items-center gap-1.5 px-2 text-sm">
                        <span className={e.kind === "spike" ? "text-good" : "text-bad"} aria-hidden="true">
                          {e.kind === "spike" ? "▲" : "▼"}
                        </span>
                        {fmt.day(e.start)} · {tc(e.kind)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </ChartFrame>

        <section aria-labelledby="attention-heading" className="space-y-3" data-testid="attention">
          <div className="flex items-baseline justify-between gap-2">
            <h2 id="attention-heading" className="text-lg font-semibold">
              {t("attention.title")}
            </h2>
            <Link href={withDataset("/insights", datasetId)} className="text-sm text-primary underline-offset-4 hover:underline">
              {t("attention.all")}
            </Link>
          </div>
          <p className="text-sm text-muted-foreground">{t("attention.body")}</p>
          {attention.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : attention.error ? (
            <ErrorState title={t("loadFailed")} error={attention.error} onRetry={() => void attention.refetch()} />
          ) : top.length ? (
            <InsightList insights={top} onOpen={setOpenInsight} compact />
          ) : (
            <p className="text-sm text-muted-foreground">{t("attention.empty")}</p>
          )}
        </section>
      </div>

      <ContributorsPanel
        dims={dims}
        loading={pareto.isPending}
        run={run}
        datasetId={datasetId}
        version={version}
      />

      <InsightDrawer id={openInsight} onClose={() => setOpenInsight(null)} />
    </div>
  );
}

function ContributorsPanel({
  dims,
  loading,
  run,
  datasetId,
  version,
}: {
  dims: ReturnType<typeof byDimension>;
  loading: boolean;
  run: AnalysisRun;
  datasetId: string;
  version: number;
}) {
  const t = useTranslations("dashboard");
  const tc = useTranslations("charts");
  const fmt = useFormat();
  const available = DIMENSIONS.filter((d) => dims[d]);
  const [tab, setTab] = useState<Dimension>("product");
  const current = dims[tab] ? tab : (available[0] ?? "product");
  const insight = dims[current];
  const items = itemsOf(insight);
  const bars = paretoBars(items);
  const skipped = run.progress.modules?.pareto?.status !== "completed";

  return (
    <ChartFrame
      testId="contributors"
      title={t("contributors.title")}
      takeaway={insight ? <EngineText>{insight.title}</EngineText> : undefined}
      unit={t("contributors.unit")}
      loading={loading}
      badges={insight ? <ConfidenceBadge level={insight.confidence} sampleSize={insight.sample_size} /> : null}
      table={
        bars.length
          ? {
              columns: [t(`contributors.dim.${current}`), tc("revenue"), tc("share"), tc("cumulative"), tc("class")],
              numeric: [1, 2, 3],
              rows: items.map((i) => [i.item, fmt.money(i.revenue), `${fmt.dec(i.share_pct)}%`, `${fmt.dec(i.cumulative_pct)}%`, i.abc]),
            }
          : undefined
      }
      actions={
        <Button asChild variant="ghost" size="sm">
          <Link href={withDataset("/insights?module=pareto", datasetId)}>
            {t("contributors.more")}
            <ArrowUpRight className="rtl:-scale-x-100" aria-hidden="true" />
          </Link>
        </Button>
      }
    >
      {skipped ? (
        <ModuleNotice run={run} module="pareto" datasetId={datasetId} version={version} />
      ) : (
        <Tabs value={current} onValueChange={(v) => setTab(v as Dimension)}>
          <TabsList>
            {DIMENSIONS.map((d) => (
              <TabsTrigger key={d} value={d} disabled={!dims[d]} data-testid={`dim-${d}`}>
                {t(`contributors.dim.${d}`)}
              </TabsTrigger>
            ))}
          </TabsList>
          {DIMENSIONS.map((d) => (
            <TabsContent key={d} value={d} className="space-y-3 pt-3">
              {dims[d] ? (
                <>
                  <BarChart
                    data={d === current ? bars : []}
                    formatValue={fmt.compact}
                    formatShare={(n) => `${fmt.dec(n)}%`}
                    ariaLabel={t("contributors.aria", { dim: t(`contributors.dim.${d}`) })}
                    tooltip={(b) => (
                      <span className="tabular-nums">
                        {fmt.money(b.value)} · {tc("cumulativeShare", { value: fmt.dec(b.cumulative ?? 0) })} ·{" "}
                        {tc(`abc.${b.group ?? "A"}`)}
                      </span>
                    )}
                  />
                  <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground" aria-label={tc("legend")}>
                    {(["solid", "hatch", "dots"] as const).map((p, i) => (
                      <li key={p} className="flex items-center gap-1.5">
                        <PatternSwatch pattern={p} />
                        {tc(`abc.${["A", "B", "C"][i]}`)}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">{t("contributors.missing", { dim: t(`contributors.dim.${d}`) })}</p>
              )}
            </TabsContent>
          ))}
        </Tabs>
      )}
    </ChartFrame>
  );
}
