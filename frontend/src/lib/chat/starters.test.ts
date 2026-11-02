import { describe, expect, it } from "vitest";

import type { Capabilities } from "@/lib/api/types";

import { selectStarters } from "./starters";

function caps(modules: string[], roles: string[] = []): Capabilities {
  const names = ["kpis", "pareto", "forecast", "anomalies", "segmentation", "churn"];
  return {
    modules: names.map((name) => ({
      name,
      title: name,
      available: modules.includes(name),
      reason: null,
      notes: [],
    })),
    roles: Object.fromEntries(roles.map((r) => [r, r])),
    version: 1,
    version_id: "v",
  } as Capabilities;
}

describe("selectStarters", () => {
  it("offers forecast, churn and anomalies only when they can run", () => {
    expect(selectStarters(caps(["kpis"]))).not.toEqual(
      expect.arrayContaining(["forecast"]),
    );
    const all = selectStarters(caps(["kpis", "forecast", "anomalies", "churn"]));
    expect(all).toEqual(expect.arrayContaining(["forecast", "anomalies", "churn"]));
  });

  it("offers what-if only with price and quantity columns", () => {
    expect(selectStarters(caps(["kpis"], ["price"]))).not.toContain("whatIf");
    expect(selectStarters(caps(["kpis"], ["price", "quantity"]))).toContain("whatIf");
  });

  it("still offers four questions on a dataset that supports almost nothing", () => {
    const starters = selectStarters(caps([]));
    expect(starters.length).toBeGreaterThanOrEqual(4);
    expect(starters).not.toContain("revenue");
  });

  it("never offers more than six", () => {
    const every = caps(["kpis", "pareto", "forecast", "anomalies", "segmentation", "churn"], ["price", "quantity"]);
    expect(selectStarters(every)).toHaveLength(6);
  });

  it("repeats nothing", () => {
    const starters = selectStarters(caps(["kpis"]));
    expect(new Set(starters).size).toBe(starters.length);
  });
});
