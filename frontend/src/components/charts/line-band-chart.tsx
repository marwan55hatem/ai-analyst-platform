"use client";

import { memo, useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { forecastStart, type TrendPoint } from "@/lib/charts/trend";

export interface LineBandLabels {
  actual: string;
  forecast: string;
  band80: string;
  band95: string;
  spike: string;
  drop: string;
  forecastStarts: string;
  partial: string;
  /** Wording for a period that contains an unusual day (week and month views). */
  tickSpike?: string;
  tickDrop?: string;
}

export interface LineBandChartProps {
  points: TrendPoint[];
  labels: LineBandLabels;
  /** Full value for tooltips (e.g. 1,234,567). */
  formatValue: (n: number) => string;
  /** Short value for the axis (e.g. 1.2M). */
  formatTick: (n: number) => string;
  formatPeriod: (iso: string) => string;
  height?: number;
  /** Accessible name of the plot (the table view carries the numbers). */
  ariaLabel: string;
  /**
   * "point" sits on the line and belongs to day views. "tick" marks the time axis instead: a
   * one-day anomaly is not a property of a weekly or monthly total, so it must not sit on it.
   */
  markerStyle?: "point" | "tick";
  /** Full date of an anomaly, for the tooltip of a tick. */
  formatDay?: (iso: string) => string;
}

const AXIS = { fontSize: 12, fill: "var(--muted-foreground)" };
const MARGIN = { top: 12, right: 12, bottom: 4, left: 4 };
const AXIS_HEIGHT = 30;

/** Triangle marker for anomalies: up for a spike, down for a drop, ringed by the card colour. */
interface MarkerProps {
  cx?: number;
  cy?: number;
  payload?: TrendPoint;
  index?: number;
}

function MarkerDot(props: MarkerProps) {
  const { cx, cy, payload, index } = props;
  if (!payload?.marker || cx === undefined || cy === undefined) return <g key={`m${index}`} />;
  const s = 7;
  const path =
    payload.marker === "spike"
      ? `M${cx},${cy - s} L${cx + s},${cy + s * 0.75} L${cx - s},${cy + s * 0.75} Z`
      : `M${cx},${cy + s} L${cx + s},${cy - s * 0.75} L${cx - s},${cy - s * 0.75} Z`;
  return (
    <path
      key={`m${index}`}
      d={path}
      fill={payload.marker === "spike" ? "var(--good)" : "var(--bad)"}
      stroke="var(--card)"
      strokeWidth={2}
      data-marker={payload.marker}
    />
  );
}

/** A small triangle standing on the time axis: this period contains an unusual day. */
function MarkerTick({ cx, axisY, payload, index }: MarkerProps & { axisY: number }) {
  if (!payload?.marker || cx === undefined) return <g key={`m${index}`} />;
  const s = 5;
  const top = axisY - 2 * s;
  const path =
    payload.marker === "spike"
      ? `M${cx},${top} L${cx + s},${axisY} L${cx - s},${axisY} Z`
      : `M${cx},${axisY} L${cx + s},${top} L${cx - s},${top} Z`;
  return (
    <path
      key={`m${index}`}
      d={path}
      fill={payload.marker === "spike" ? "var(--good)" : "var(--bad)"}
      stroke="var(--card)"
      strokeWidth={1}
      data-marker-tick={payload.marker}
    />
  );
}

function TooltipBody({
  active,
  payload,
  labels,
  formatValue,
  formatPeriod,
  markerStyle,
  formatDay,
}: { active?: boolean; payload?: readonly { payload?: unknown }[] } & Pick<
  LineBandChartProps,
  "labels" | "formatValue" | "formatPeriod" | "markerStyle" | "formatDay"
>) {
  const point = payload?.[0]?.payload as TrendPoint | undefined;
  if (!active || !point) return null;
  return (
    <div className="space-y-1 rounded-md border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-lg">
      <p className="font-medium">
        {formatPeriod(point.period)}
        {point.partial ? <span className="font-normal text-muted-foreground"> · {labels.partial}</span> : null}
      </p>
      {point.actual !== null ? (
        <p className="tabular-nums">
          {labels.actual}: {formatValue(point.actual)}
        </p>
      ) : null}
      {point.actual === null && point.forecast !== null ? (
        <p className="tabular-nums">
          {labels.forecast}: {formatValue(point.forecast)}
        </p>
      ) : null}
      {point.band80 ? (
        <p className="text-muted-foreground tabular-nums">
          {labels.band80}: {formatValue(point.band80[0])} – {formatValue(point.band80[1])}
        </p>
      ) : null}
      {point.band95 ? (
        <p className="text-muted-foreground tabular-nums">
          {labels.band95}: {formatValue(point.band95[0])} – {formatValue(point.band95[1])}
        </p>
      ) : null}
      {point.marker ? (
        <p className={point.marker === "spike" ? "text-good" : "text-bad"}>
          {point.marker === "spike" ? "▲ " : "▼ "}
          {markerStyle === "tick"
            ? `${(point.marker === "spike" ? labels.tickSpike : labels.tickDrop) ?? ""} · ${
                point.event ? (formatDay ?? formatPeriod)(point.event.start) : ""
              }`
            : point.marker === "spike"
              ? labels.spike
              : labels.drop}
        </p>
      ) : null}
    </div>
  );
}

/**
 * History line, dashed forecast with 80% and 95% ranges, and anomaly triangles.
 * Time runs left to right in both languages (docs/design.md "Charts"); animation is off.
 */
export const LineBandChart = memo(function LineBandChart({
  points,
  labels,
  formatValue,
  formatTick,
  formatPeriod,
  height = 280,
  ariaLabel,
  markerStyle = "point",
  formatDay,
}: LineBandChartProps) {
  const start = useMemo(() => forecastStart(points), [points]);
  const hasForecast = start >= 0;
  const hasMarkers = useMemo(() => points.some((p) => p.marker), [points]);
  return (
    <div className="space-y-2">
      <Legend labels={labels} forecast={hasForecast} markers={hasMarkers} markerStyle={markerStyle} />
      <div dir="ltr" role="img" aria-label={ariaLabel} style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 640, height }}>
          <ComposedChart data={points} margin={MARGIN} accessibilityLayer={false}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis
              dataKey="period"
              tickFormatter={formatPeriod}
              tick={AXIS}
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
              minTickGap={24}
              height={AXIS_HEIGHT}
            />
            <YAxis
              tickFormatter={formatTick}
              tick={AXIS}
              tickLine={false}
              axisLine={false}
              width={56}
            />
            <Tooltip
              cursor={{ stroke: "var(--input)", strokeDasharray: "3 3" }}
              content={(props) => (
                <TooltipBody
                  active={props.active}
                  payload={props.payload as readonly { payload?: unknown }[] | undefined}
                  labels={labels}
                  formatValue={formatValue}
                  formatPeriod={formatPeriod}
                  markerStyle={markerStyle}
                  formatDay={formatDay}
                />
              )}
            />
            {hasForecast ? (
              <>
                <Area
                  dataKey="band95"
                  stroke="none"
                  fill="var(--chart-band-95)"
                  isAnimationActive={false}
                  connectNulls={false}
                  activeDot={false}
                />
                <Area
                  dataKey="band80"
                  stroke="none"
                  fill="var(--chart-band-80)"
                  isAnimationActive={false}
                  connectNulls={false}
                  activeDot={false}
                />
                <ReferenceLine
                  x={points[start]?.period}
                  stroke="var(--input)"
                  strokeDasharray="2 4"
                  label={{ value: labels.forecastStarts, position: "insideTopLeft", fontSize: 12, fill: "var(--muted-foreground)" }}
                />
                <Line
                  dataKey="forecast"
                  stroke="var(--chart-forecast)"
                  strokeWidth={2}
                  strokeDasharray="6 4"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls={false}
                />
              </>
            ) : null}
            <Line
              dataKey="actual"
              stroke="var(--chart-actual)"
              strokeWidth={2}
              dot={
                hasMarkers
                  ? (p) =>
                      markerStyle === "tick" ? (
                        <MarkerTick
                          key={`m${p.index}`}
                          cx={typeof p.cx === "number" ? p.cx : undefined}
                          axisY={height - MARGIN.bottom - AXIS_HEIGHT}
                          payload={p.payload as TrendPoint}
                          index={p.index}
                        />
                      ) : (
                        <MarkerDot
                          key={`m${p.index}`}
                          cx={typeof p.cx === "number" ? p.cx : undefined}
                          cy={typeof p.cy === "number" ? p.cy : undefined}
                          payload={p.payload as TrendPoint}
                          index={p.index}
                        />
                      )
                  : false
              }
              activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }}
              isAnimationActive={false}
              connectNulls={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
});

/** A band as it looks in the chart: the 80% range is drawn over the 95% range. */
function BandSwatch({ inner = false }: { inner?: boolean }) {
  return (
    <svg width="20" height="12" aria-hidden="true" data-testid={inner ? "swatch-80" : "swatch-95"}>
      <rect width="20" height="12" fill="var(--chart-band-95)" />
      {inner ? <rect width="20" height="12" fill="var(--chart-band-80)" /> : null}
      <rect x="0.5" y="0.5" width="19" height="11" fill="none" stroke="var(--chart-forecast)" />
    </svg>
  );
}

/** Legend with the line style of each series, so identity never rests on colour alone. */
function Legend({
  labels,
  forecast,
  markers,
  markerStyle,
}: {
  labels: LineBandLabels;
  forecast: boolean;
  markers: boolean;
  markerStyle: "point" | "tick";
}) {
  const ticks = markerStyle === "tick";
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-hidden="true">
      <li className="flex items-center gap-1.5">
        <svg width="20" height="8">
          <line x1="0" y1="4" x2="20" y2="4" stroke="var(--chart-actual)" strokeWidth="2" />
        </svg>
        {labels.actual}
      </li>
      {forecast ? (
        <>
          <li className="flex items-center gap-1.5">
            <svg width="20" height="8">
              <line x1="0" y1="4" x2="20" y2="4" stroke="var(--chart-forecast)" strokeWidth="2" strokeDasharray="6 4" />
            </svg>
            {labels.forecast}
          </li>
          <li className="flex items-center gap-1.5">
            <BandSwatch inner />
            {labels.band80}
          </li>
          <li className="flex items-center gap-1.5">
            <BandSwatch />
            {labels.band95}
          </li>
        </>
      ) : null}
      {markers ? (
        <>
          <li className="flex items-center gap-1.5">
            <span className="text-good">▲</span>
            {ticks ? labels.tickSpike : labels.spike}
          </li>
          <li className="flex items-center gap-1.5">
            <span className="text-bad">▼</span>
            {ticks ? labels.tickDrop : labels.drop}
          </li>
        </>
      ) : null}
    </ul>
  );
}
