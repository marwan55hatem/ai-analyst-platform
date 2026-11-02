import type { ChartTable } from "@/components/charts/chart-frame";
import type { BarDatum } from "@/lib/charts/pareto";
import type { ActualPoint } from "@/lib/charts/trend";

import type { Cell, ChartSpec } from "./events";

interface Shared {
  table: ChartTable;
  truncated: boolean;
  /** Series that stay in the table only: each wrapper draws a single series. */
  dropped: string[];
}

export type ChartView = Shared &
  (
    | { kind: "kpi"; label: string; value: Cell }
    | { kind: "line"; points: ActualPoint[]; series: string }
    | { kind: "bar"; bars: BarDatum[]; series: string }
    | { kind: "table" }
  );

function shown(cell: Cell | undefined): string | number {
  if (cell === null || cell === undefined) return "—";
  return typeof cell === "boolean" ? String(cell) : cell;
}

function buildTable(columns: string[], rows: Cell[][]): ChartTable {
  const numeric = columns.flatMap((_, i) => (rows.some((r) => typeof r[i] === "number") ? [i] : []));
  return { columns, rows: rows.map((row) => row.map(shown)), numeric };
}

/** Decide how a chart spec is drawn with the existing wrappers. Values are copied, never computed. */
export function toChartView(spec: ChartSpec): ChartView {
  const base = { truncated: spec.truncated, dropped: [] as string[] };
  if (spec.type === "kpi") {
    const table = buildTable(spec.columns, [[spec.value]]);
    return { ...base, kind: "kpi", label: spec.label, value: spec.value, table };
  }
  if (spec.type === "table") {
    return { ...base, kind: "table", table: buildTable(spec.columns, spec.rows) };
  }
  const table = buildTable(
    spec.columns,
    spec.data.map((record) => spec.columns.map((column) => record[column] ?? null)),
  );
  const [series = "", ...dropped] = spec.y;
  const present = spec.data.filter((record) => typeof record[series] === "number");
  if (spec.type === "line") {
    const points = present.map((r) => ({ period: String(r[spec.x]), value: r[series] as number }));
    return { ...base, kind: "line", points, series, dropped, table };
  }
  if (spec.type === "bar") {
    const bars = present.map((r) => ({ label: String(r[spec.x]), value: r[series] as number }));
    return { ...base, kind: "bar", bars, series, dropped, table };
  }
  return { ...base, kind: "table", table }; // no scatter wrapper exists; the table has every value
}
