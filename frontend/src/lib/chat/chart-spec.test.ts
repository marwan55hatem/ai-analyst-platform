import { describe, expect, it } from "vitest";

import { toChartView } from "./chart-spec";

const base = { title: "T", truncated: false };

describe("toChartView", () => {
  it("reads a kpi", () => {
    const view = toChartView({ ...base, type: "kpi", columns: ["total"], label: "total", value: 12.5 });
    expect(view).toMatchObject({ kind: "kpi", label: "total", value: 12.5 });
  });

  it("reads a line with its table", () => {
    const view = toChartView({
      ...base,
      type: "line",
      columns: ["month", "amount"],
      x: "month",
      y: ["amount"],
      data: [
        { month: "2024-01-01", amount: 10 },
        { month: "2024-02-01", amount: 20 },
      ],
    });
    expect(view.kind).toBe("line");
    if (view.kind !== "line") return;
    expect(view.points).toEqual([
      { period: "2024-01-01", value: 10 },
      { period: "2024-02-01", value: 20 },
    ]);
    expect(view.table.rows).toEqual([
      ["2024-01-01", 10],
      ["2024-02-01", 20],
    ]);
    expect(view.table.numeric).toEqual([1]);
    expect(view.dropped).toEqual([]);
  });

  it("leaves a gap in a line for a null, never a zero", () => {
    const view = toChartView({
      ...base,
      type: "line",
      columns: ["month", "amount"],
      x: "month",
      y: ["amount"],
      data: [
        { month: "2024-01-01", amount: null },
        { month: "2024-02-01", amount: 20 },
      ],
    });
    if (view.kind !== "line") throw new Error("expected a line");
    expect(view.points).toEqual([{ period: "2024-02-01", value: 20 }]);
    expect(view.table.rows[0]).toEqual(["2024-01-01", "—"]);
  });

  it("charts the first series and names the rest", () => {
    const view = toChartView({
      ...base,
      type: "bar",
      columns: ["item", "sales", "units"],
      x: "item",
      y: ["sales", "units"],
      data: [{ item: "A", sales: 5, units: 2 }],
    });
    if (view.kind !== "bar") throw new Error("expected a bar");
    expect(view.bars).toEqual([{ label: "A", value: 5 }]);
    expect(view.dropped).toEqual(["units"]);
    expect(view.table.rows).toEqual([["A", 5, 2]]);
  });

  it("shows a scatter as a table", () => {
    const view = toChartView({
      ...base,
      type: "scatter",
      columns: ["a", "b"],
      x: "a",
      y: ["b"],
      data: [{ a: 1, b: 2 }],
    });
    expect(view.kind).toBe("table");
    expect(view.table).toMatchObject({ columns: ["a", "b"], rows: [[1, 2]], numeric: [0, 1] });
  });

  it("reads a plain table", () => {
    const view = toChartView({ ...base, type: "table", columns: ["a"], rows: [["x"], [null]] });
    expect(view.kind).toBe("table");
    expect(view.table.rows).toEqual([["x"], ["—"]]);
  });

  it("passes on that the data was cut", () => {
    const view = toChartView({ ...base, truncated: true, type: "table", columns: ["a"], rows: [] });
    expect(view.truncated).toBe(true);
  });
});
