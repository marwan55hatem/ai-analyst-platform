import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { KpiCard } from "@/components/dashboard/kpi-card";
import type { Kpi } from "@/lib/api/types";
import { rfmGrid } from "@/lib/charts/rfm";
import { buildTrend } from "@/lib/charts/trend";
import { renderWithProviders } from "@/test/render";

import { BarChart } from "./bar-chart";
import { ChartFrame } from "./chart-frame";
import { Heatmap } from "./heatmap";
import { LineBandChart } from "./line-band-chart";
import { RangeBar } from "./range-bar";
import { sparkPath, Sparkline } from "./sparkline";

const LABELS = {
  actual: "Net revenue",
  forecast: "Forecast",
  band80: "80% range",
  band95: "95% range",
  spike: "Unusual spike",
  drop: "Unusual drop",
  forecastStarts: "Forecast",
  partial: "partly in range",
};

describe("<ChartFrame>", () => {
  it("switches between the chart and a table of the same numbers", async () => {
    renderWithProviders(
      <ChartFrame title="Revenue" takeaway="Up 5%" table={{ columns: ["Month", "Revenue"], rows: [["Jan", 10]], numeric: [1] }}>
        <div data-testid="plot" />
      </ChartFrame>,
    );
    expect(screen.getByRole("figure", { name: "Revenue" })).toBeInTheDocument();
    expect(screen.getByTestId("plot")).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "View as table" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Revenue" })).toHaveClass("text-end");
    expect(within(table).getByRole("cell", { name: "10" })).toHaveClass("tabular-nums");
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View as chart" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows a skeleton while loading and no table switch", () => {
    renderWithProviders(
      <ChartFrame title="Revenue" loading table={{ columns: ["a"], rows: [] }}>
        <div data-testid="plot" />
      </ChartFrame>,
    );
    expect(screen.getByLabelText("Loading chart")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View as table" })).not.toBeInTheDocument();
  });
});

describe("<LineBandChart>", () => {
  it("names every series in the legend, including the ranges and markers", () => {
    const points = buildTrend(
      [
        { period: "2024-01-01", value: 10 },
        { period: "2024-02-01", value: 12 },
      ],
      [{ period: "2024-03-01", point: 13, lo80: 11, hi80: 15, lo95: 9, hi95: 17 }],
      [{ start: "2024-01-03", end: "2024-01-03", kind: "drop", actual: 1, expected: 9, change: -8, peak_z: -5, length: 1, insight_id: null }],
    );
    renderWithProviders(
      <LineBandChart
        points={points}
        labels={LABELS}
        formatValue={String}
        formatTick={String}
        formatPeriod={(p) => p.slice(0, 7)}
        ariaLabel="Revenue chart"
      />,
    );
    expect(screen.getByRole("img", { name: "Revenue chart" })).toBeInTheDocument();
    for (const name of ["Net revenue", "Forecast", "80% range", "95% range", "Unusual spike", "Unusual drop"]) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }
  });

  it("shows only the actual line when there is no forecast or marker", () => {
    const points = buildTrend([
      { period: "2024-01-01", value: 10 },
      { period: "2024-02-01", value: 12 },
    ]);
    renderWithProviders(
      <LineBandChart points={points} labels={LABELS} formatValue={String} formatTick={String} formatPeriod={String} ariaLabel="c" />,
    );
    expect(screen.queryByText("80% range")).not.toBeInTheDocument();
    expect(screen.queryByText("Unusual drop")).not.toBeInTheDocument();
  });
});

describe("<BarChart>", () => {
  it("prints each value and share next to its bar, largest bar full width", () => {
    renderWithProviders(
      <BarChart
        data={[
          { label: "Furniture", value: 200, share: 66.7, pattern: "solid" },
          { label: "Office", value: 100, share: 33.3, pattern: "hatch" },
        ]}
        formatValue={(n) => `${n}`}
        formatShare={(n) => `${n}%`}
        ariaLabel="Sales by category"
      />,
    );
    const list = screen.getByRole("list", { name: "Sales by category" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Furniture20066.7%");
    const bars = list.querySelectorAll<HTMLElement>("span[style]");
    expect(bars[0]?.style.width).toBe("100%");
    expect(bars[1]?.style.width).toBe("50%");
    expect(bars[1]).toHaveClass("pattern-hatch");
  });
});

describe("<Heatmap>", () => {
  it("is a table with row and column headers and the count in every cell", () => {
    renderWithProviders(
      <Heatmap
        rows={rfmGrid([{ r_score: 5, f_score: 5, customers: 12, monetary: 1 }])}
        caption="Customers by recency and frequency"
        rowTitle="Recency"
        colTitle="Frequency"
        rowLabel={(r) => `R ${r}`}
        colLabel={(c) => `F ${c}`}
        cellText={(c) => (c.customers ? String(c.customers) : "–")}
      />,
    );
    const table = screen.getByRole("table", { name: "Customers by recency and frequency" });
    expect(within(table).getAllByRole("rowheader")).toHaveLength(5);
    expect(within(table).getByRole("columnheader", { name: "F 3" })).toBeInTheDocument();
    const filled = within(table).getByRole("cell", { name: "12" });
    expect(filled).toHaveAttribute("data-level", "4");
    expect(within(table).getAllByRole("cell", { name: "–" })).toHaveLength(24);
  });
});

describe("<Sparkline> and <RangeBar>", () => {
  it("draws a path across the box and nothing for fewer than two points", () => {
    expect(sparkPath([1, 2, 3])).toBe("M0.00,29.00 L50.00,16.00 L100.00,3.00");
    expect(sparkPath([5])).toBe("");
    const { container } = renderWithProviders(<Sparkline values={[1, 3, 2]} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("prints low, expected and high under the range", () => {
    renderWithProviders(
      <RangeBar
        band={{ low: -10, expected: 5, high: 20 }}
        domain={[-10, 20]}
        format={String}
        labels={{ low: "Low", expected: "Expected", high: "High" }}
      />,
    );
    expect(screen.getByText("Low").nextSibling).toHaveTextContent("-10");
    expect(screen.getByText("Expected").nextSibling).toHaveTextContent("5");
    expect(screen.getByText("High").nextSibling).toHaveTextContent("20");
  });
});

const KPI: Kpi = {
  key: "net_revenue",
  unit: "money",
  available: true,
  value: 6_200_000,
  previous_value: 3_380_000,
  change: 83.5,
  change_unit: "percent",
  reason: null,
};

describe("<KpiCard>", () => {
  it("states the value and the change in words, marked good", () => {
    renderWithProviders(<KpiCard kpi={KPI} spark={[1, 2, 3]} previousLabel="previous period" />);
    const card = screen.getByRole("article", { name: "Net revenue" });
    expect(within(card).getByTestId("kpi-value")).toHaveTextContent("6.2M");
    const change = card.querySelector("[data-direction]");
    expect(change).toHaveAttribute("data-direction", "up");
    expect(change).toHaveClass("text-good");
    expect(change).toHaveTextContent("+83.5%");
    expect(within(card).getByTestId("kpi-comparison")).toHaveTextContent("vs previous period");
    expect(within(card).getByTestId("sparkline")).toBeInTheDocument();
  });

  it("says so when there is nothing to compare with", () => {
    renderWithProviders(
      <KpiCard kpi={{ ...KPI, change: null, previous_value: null, reason: "No data before." }} spark={[]} previousLabel="x" />,
    );
    expect(screen.getByText("No earlier period to compare")).toBeInTheDocument();
  });

  it("explains a measure the file cannot give", () => {
    renderWithProviders(
      <KpiCard
        kpi={{ ...KPI, key: "margin_pct", unit: "percent", available: false, value: null, change: null, reason: "The file has no cost column." }}
        spark={[]}
        previousLabel="x"
      />,
    );
    expect(screen.getByText("The file has no cost column.")).toHaveAttribute("lang", "en");
  });

  it("speaks Arabic with Latin digits and points for margin", () => {
    renderWithProviders(
      <KpiCard
        kpi={{ ...KPI, key: "margin_pct", unit: "percent", value: 29.04, change: -0.1, change_unit: "points" }}
        spark={[1, 2]}
        previousLabel="الفترة السابقة"
      />,
      { locale: "ar" },
    );
    const card = screen.getByRole("article", { name: "هامش الربح الإجمالي" });
    expect(within(card).getByTestId("kpi-value")).toHaveTextContent("29%");
    const change = card.querySelector("[data-direction]");
    expect(change).toHaveClass("text-bad");
    expect(change).toHaveTextContent("-0.1 نقطة");
  });
});
