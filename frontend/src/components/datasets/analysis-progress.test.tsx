import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AnalysisRun } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";

import { AnalysisProgress, moduleRows } from "./analysis-progress";

function run(partial: Partial<AnalysisRun>): AnalysisRun {
  return {
    id: "r1",
    dataset_version_id: "v1",
    status: "running",
    progress: { total: 9, done: 0, modules: {} },
    config: {},
    started_at: "2026-10-28T10:00:00Z",
    finished_at: null,
    error: null,
    created_at: "2026-10-28T10:00:00Z",
    ...partial,
  };
}

describe("moduleRows", () => {
  it("marks the first unfinished module as running", () => {
    const rows = moduleRows(
      run({ progress: { total: 9, done: 1, modules: { kpis: { status: "completed", insights: 3, warnings: [] } } } }),
    );
    expect(rows.slice(0, 3).map((r) => r.state)).toEqual(["completed", "running", "waiting"]);
  });

  it("shows nothing as running for a queued run", () => {
    expect(moduleRows(run({ status: "pending" })).every((r) => r.state === "waiting")).toBe(true);
  });
});

describe("<AnalysisProgress>", () => {
  it("shows skipped and failed modules with the backend's reasons", () => {
    renderWithProviders(
      <AnalysisProgress
        run={run({
          status: "completed_with_warnings",
          finished_at: "2026-10-28T10:01:00Z",
          progress: {
            total: 9,
            done: 9,
            modules: {
              kpis: { status: "completed", insights: 2, warnings: [] },
              cohort_retention: {
                status: "skipped",
                reason: "Needs a customer id column",
                insights: 0,
                warnings: [],
              },
              churn: {
                status: "failed",
                error: "ValueError: x",
                insights: 0,
                warnings: ["The churn risk module failed and was skipped: ValueError: x"],
              },
            },
          },
        })}
      />,
    );
    expect(screen.getByText("Analysis complete, some modules skipped")).toBeInTheDocument();
    expect(screen.getByText("Needs a customer id column")).toHaveAttribute("lang", "en");
    expect(screen.getByText(/The churn risk module failed/)).toBeInTheDocument();
    expect(screen.getByText("Done · 2 findings")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-progress")).toHaveAttribute("data-status", "completed_with_warnings");
  });

  it("shows live progress in Arabic", () => {
    renderWithProviders(<AnalysisProgress run={run({ progress: { total: 9, done: 4, modules: {} } })} />, {
      locale: "ar",
    });
    expect(screen.getByText("قيد التشغيل · 4 من 9 وحدات")).toBeInTheDocument();
  });
});
