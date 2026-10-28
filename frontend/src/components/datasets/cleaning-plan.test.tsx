import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { Fix } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { CleaningPlan, initialChoices, toApplyBody } from "./cleaning-plan";

function fix(partial: Partial<Fix> & Pick<Fix, "id" | "type" | "risk">): Fix {
  return {
    description: `${partial.type} description`,
    columns: ["region"],
    rows_affected: 10,
    cells_affected: 10,
    recommended: false,
    strategies: null,
    default_strategy: null,
    strategy_risk: null,
    params: {},
    ...partial,
  };
}

const FIXES: Fix[] = [
  fix({ id: "trim:region", type: "trim", risk: "low", recommended: true }),
  fix({ id: "flag_outliers:qty", type: "flag_outliers", risk: "low", recommended: false, columns: ["qty"] }),
  fix({ id: "cast:price", type: "cast", risk: "medium", recommended: true, columns: ["price"] }),
  fix({ id: "dedupe", type: "dedupe", risk: "medium", recommended: false, columns: [] }),
  fix({
    id: "nulls:region",
    type: "nulls",
    risk: "medium",
    strategies: ["keep", "impute_mode", "drop_rows"],
    default_strategy: "keep",
    strategy_risk: { impute_mode: "medium", drop_rows: "high" },
  }),
  fix({
    id: "parse_dates:order_date",
    type: "parse_dates",
    risk: "high",
    recommended: true,
    columns: ["order_date"],
    strategies: ["dayfirst", "monthfirst"],
    default_strategy: "dayfirst",
  }),
];

describe("initialChoices", () => {
  it("pre-selects only low-risk recommended fixes", () => {
    const choices = initialChoices(FIXES);
    const selected = Object.entries(choices).filter(([, c]) => c.selected).map(([id]) => id);
    expect(selected).toEqual(["trim:region"]);
  });

  it("never pre-selects medium or high risk, even when recommended", () => {
    const choices = initialChoices(FIXES);
    expect(choices["cast:price"]?.selected).toBe(false);
    expect(choices["parse_dates:order_date"]?.selected).toBe(false);
  });

  it("starts null handling at keep and dates at the detected order", () => {
    const choices = initialChoices(FIXES);
    expect(choices["nulls:region"]?.strategy).toBe("keep");
    expect(choices["parse_dates:order_date"]?.strategy).toBe("dayfirst");
  });

  it("sends strategies only for fixes that have them", () => {
    const choices = initialChoices(FIXES);
    choices["dedupe"] = { selected: true, strategy: null };
    choices["nulls:region"] = { selected: true, strategy: "drop_rows" };
    expect(toApplyBody(FIXES, choices)).toEqual([
      { id: "trim:region" },
      { id: "dedupe" },
      { id: "nulls:region", strategy: "drop_rows" },
    ]);
  });
});

describe("<CleaningPlan>", () => {
  it("shows risk badges, null strategies and both date orders", () => {
    renderWithProviders(<CleaningPlan datasetId="d1" version={1} fixes={FIXES} />);
    expect(screen.getByRole("checkbox", { name: /Trim spaces/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Convert to numbers/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Read as dates/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Keep them empty" })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Day first/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Month first/ })).not.toBeChecked();
    expect(screen.getAllByText("High risk").length).toBeGreaterThan(0);
    expect(screen.getByText("1 fix selected")).toBeInTheDocument();
  });

  it("applies the selected fixes, including dedupe when ticked", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/v1/datasets/d1/cleaning-plan/apply", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ id: "d1", latest_version: { version: 2 } }, { status: 202 });
      }),
      http.get("*/api/v1/datasets*", () => HttpResponse.json({ id: "d1" })),
    );
    const user = userEvent.setup();
    renderWithProviders(<CleaningPlan datasetId="d1" version={1} fixes={FIXES} />);
    await user.click(screen.getByRole("checkbox", { name: /Remove duplicate rows/ }));
    const medium = screen.getByRole("region", { name: "Medium risk" });
    expect(within(medium).getByRole("checkbox", { name: /Remove duplicate rows/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Apply fixes" }));
    await vi.waitFor(() => expect(body).toEqual({ fixes: [{ id: "trim:region" }, { id: "dedupe" }] }));
  });
});
