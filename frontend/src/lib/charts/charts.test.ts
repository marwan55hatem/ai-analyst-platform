import { describe, expect, it } from "vitest";

import type { AnomalyEvent, Insight, Kpi, SeriesPoint } from "@/lib/api/types";

import { direction, formatChange, formatValue, isGood, sparkValues, type Formatters } from "./kpi";
import { byDimension, itemsOf, paretoBars } from "./pareto";
import { autoGrain, daysIn, grainAllowed, presetRange, severity, validDay } from "./range";
import { rfmGrid } from "./rfm";
import { buildTrend, forecastStart, periodOf } from "./trend";
import { clampChange, isComplete, position, sharedDomain } from "./whatif";

const fmt: Formatters = {
  compact: (n) => `${(n / 1_000_000).toFixed(1)}M`,
  money: (n) => n.toFixed(0),
  int: (n) => String(Math.round(n)),
  dec: (n) => n.toFixed(1),
  signedPct: (n) => `${n > 0 ? "+" : ""}${n.toFixed(1)}%`,
  signed: (n) => `${n > 0 ? "+" : ""}${n.toFixed(1)}`,
};

function kpi(over: Partial<Kpi>): Kpi {
  return {
    key: "net_revenue",
    unit: "money",
    available: true,
    value: 100,
    previous_value: 80,
    change: 25,
    change_unit: "percent",
    reason: null,
    ...over,
  };
}

describe("kpi logic", () => {
  it("reads the direction of a change, with a flat band and no change at all", () => {
    expect(direction(kpi({ change: 5 }))).toBe("up");
    expect(direction(kpi({ change: -3 }))).toBe("down");
    expect(direction(kpi({ change: 0.01 }))).toBe("flat");
    expect(direction(kpi({ change: null }))).toBe("none");
  });

  it("calls a rise good news and has no verdict without a change", () => {
    expect(isGood("orders", "up")).toBe(true);
    expect(isGood("margin_pct", "down")).toBe(false);
    expect(isGood("aov", "flat")).toBeNull();
    expect(isGood("aov", "none")).toBeNull();
  });

  it("prints values by unit and shortens large ones", () => {
    expect(formatValue(kpi({ value: 6_200_000 }), fmt)).toBe("6.2M");
    expect(formatValue(kpi({ value: 774 }), fmt)).toBe("774");
    expect(formatValue(kpi({ unit: "percent", value: 28.94 }), fmt)).toBe("28.9%");
    expect(formatValue(kpi({ value: null }), fmt)).toBe("—");
  });

  it("prints margin changes as points and others as percent", () => {
    expect(formatChange(kpi({ change: 83.5 }), fmt)).toBe("+83.5%");
    expect(formatChange(kpi({ change: -0.1, change_unit: "points" }), fmt)).toBe("-0.1");
    expect(formatChange(kpi({ change: null }), fmt)).toBeNull();
  });

  it("skips periods without a value in the sparkline", () => {
    const series = [
      { period: "2024-01-01", aov: 10 },
      { period: "2024-01-02", aov: null },
      { period: "2024-01-03", aov: 12 },
    ] as unknown as SeriesPoint[];
    expect(sparkValues(series, "aov")).toEqual([10, 12]);
  });
});

const EVENT = (start: string, kind: "spike" | "drop", change: number): AnomalyEvent => ({
  start,
  end: start,
  kind,
  actual: 0,
  expected: 0,
  change,
  peak_z: 0,
  length: 1,
  insight_id: null,
});

describe("trend", () => {
  const actual = [
    { period: "2024-01-01", value: 10 },
    { period: "2024-02-01", value: 12 },
    { period: "2024-03-01", value: 14, partial: true },
  ];
  const forecast = [
    { period: "2024-04-01", point: 15, lo80: 13, hi80: 17, lo95: 11, hi95: 19 },
    { period: "2024-05-01", point: 16, lo80: 13, hi80: 19, lo95: 10, hi95: 22 },
  ];

  it("joins the forecast to the last actual point and keeps the ranges", () => {
    const points = buildTrend(actual, forecast);
    expect(points).toHaveLength(5);
    expect(points[2]).toMatchObject({ actual: 14, forecast: 14, partial: true });
    expect(points[3]).toMatchObject({ actual: null, forecast: 15, band80: [13, 17], band95: [11, 19] });
    expect(forecastStart(points)).toBe(3);
    expect(forecastStart(buildTrend(actual))).toBe(-1);
  });

  it("marks the largest event of a period and drops events after the range", () => {
    const points = buildTrend(
      actual,
      [],
      [EVENT("2024-02-10", "drop", -50), EVENT("2024-02-20", "spike", 80), EVENT("2024-06-01", "spike", 999)],
      "2024-03-31",
    );
    expect(points[1]).toMatchObject({ marker: "spike" });
    expect(points[1]?.event?.change).toBe(80);
    expect(points.filter((p) => p.marker)).toHaveLength(1);
  });

  it("finds the period an event belongs to", () => {
    expect(periodOf("2024-02-15", ["2024-01-01", "2024-02-01", "2024-03-01"])).toBe("2024-02-01");
    expect(periodOf("2023-12-31", ["2024-01-01"])).toBeNull();
  });
});

describe("pareto and rfm", () => {
  const insight = {
    type: "pareto",
    payload: {
      dimension: "category",
      items: [
        { item: "A", revenue: 50, share_pct: 50, cumulative_pct: 50, abc: "A", returns: 0 },
        { item: "B", revenue: 30, share_pct: 30, cumulative_pct: 80, abc: "B", returns: 0 },
        { item: "C", revenue: 20, share_pct: 20, cumulative_pct: 100, abc: "C", returns: 0 },
      ],
    },
  } as unknown as Insight;

  it("turns ABC classes into textures so groups do not rely on colour", () => {
    const bars = paretoBars(itemsOf(insight));
    expect(bars.map((b) => b.pattern)).toEqual(["solid", "hatch", "dots"]);
    expect(bars[1]).toMatchObject({ label: "B", value: 30, share: 30, cumulative: 80 });
  });

  it("keeps one Pareto insight per dimension and ignores other types", () => {
    const dead = { ...insight, type: "dead_stock" } as Insight;
    expect(Object.keys(byDimension([dead, insight]))).toEqual(["category"]);
    expect(itemsOf(undefined)).toEqual([]);
  });

  it("builds a full 5x5 grid with recency 5 on top and fill levels", () => {
    const grid = rfmGrid([
      { r_score: 5, f_score: 5, customers: 40, monetary: 1 },
      { r_score: 1, f_score: 1, customers: 10, monetary: 1 },
    ]);
    expect(grid).toHaveLength(5);
    expect(grid[0]?.[4]).toMatchObject({ row: 5, col: 5, customers: 40, share: 80, level: 4 });
    expect(grid[4]?.[0]).toMatchObject({ row: 1, col: 1, level: 1 });
    expect(grid[2]?.[2]).toMatchObject({ customers: 0, level: 0 });
  });
});

describe("date ranges", () => {
  const data = { start: "2023-01-06", end: "2024-12-31" };

  it("counts presets back from the end of the data and trims them to it", () => {
    expect(presetRange("30d", data)).toEqual({ start: "2024-12-02", end: "2024-12-31" });
    expect(presetRange("ytd", data)).toEqual({ start: "2024-01-01", end: "2024-12-31" });
    expect(presetRange("all", data)).toEqual(data);
    expect(presetRange("12m", { start: "2024-06-01", end: "2024-12-31" }).start).toBe("2024-06-01");
  });

  it("chooses a grain by length and refuses grains over the point limit", () => {
    expect(autoGrain({ start: "2024-10-03", end: "2024-12-31" })).toBe("day");
    expect(autoGrain(data)).toBe("week");
    expect(autoGrain({ start: "2015-01-01", end: "2024-12-31" })).toBe("month");
    expect(grainAllowed({ start: "2015-01-01", end: "2024-12-31" }, "day")).toBe(false);
    expect(daysIn({ start: "2024-01-01", end: "2024-01-31" })).toBe(31);
  });

  it("accepts only real dates and clamps them into the data", () => {
    expect(validDay("2024-05-01", data)).toBe("2024-05-01");
    expect(validDay("2020-01-01", data)).toBe("2023-01-06");
    expect(validDay("not-a-date", data)).toBeNull();
    expect(validDay(null, data)).toBeNull();
  });

  it("maps impact scores to severity", () => {
    expect(severity(60)).toBe("high");
    expect(severity(20)).toBe("medium");
    expect(severity(5)).toBe("low");
  });
});

describe("what-if rules", () => {
  it("clamps changes to whole percents within +/-30", () => {
    expect(clampChange(45)).toBe(30);
    expect(clampChange("-31")).toBe(-30);
    expect(clampChange("7.6")).toBe(8);
    expect(clampChange("abc")).toBe(0);
  });

  it("needs a target for category and product scopes", () => {
    expect(isComplete({ lever: "price", change_pct: 5, scope: "all" })).toBe(true);
    expect(isComplete({ lever: "price", change_pct: 5, scope: "category", target: null })).toBe(false);
    expect(isComplete({ lever: "price", change_pct: 5, scope: "product", target: "Item-07" })).toBe(true);
  });

  it("shares one domain across ranges that always includes zero", () => {
    const domain = sharedDomain([
      { low: 10, expected: 20, high: 30 },
      { low: -5, expected: 0, high: 5 },
      null,
    ]);
    expect(domain).toEqual([-5, 30]);
    expect(position(0, domain)).toBeCloseTo((5 / 35) * 100);
    expect(sharedDomain([])).toEqual([-1, 1]);
  });
});
