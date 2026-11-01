import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ToolStep } from "@/lib/chat/events";
import { renderWithProviders } from "@/test/render";

import { HowIGotThis } from "./how-i-got-this";

const step = (over: Partial<ToolStep>): ToolStep => ({
  id: "c1",
  name: "run_sql",
  arguments: {},
  ok: true,
  summary: "2 rows",
  evidence: {},
  ...over,
});

const SQL = step({
  evidence: {
    sql: "SELECT region, SUM(revenue) FROM data GROUP BY 1",
    columns: ["region", "total"],
    rows: [
      ["North", 10],
      ["South", 20],
    ],
    row_count: 2,
    truncated: false,
  },
});

async function open(tools: ToolStep[], onOpenInsight = vi.fn()) {
  renderWithProviders(<HowIGotThis tools={tools} onOpenInsight={onOpenInsight} />);
  await userEvent.click(screen.getByRole("button", { name: /how i got this/i }));
}

describe("HowIGotThis", () => {
  it("is collapsed by default and mounts no evidence until opened", () => {
    renderWithProviders(<HowIGotThis tools={[SQL]} onOpenInsight={vi.fn()} />);
    expect(screen.getByRole("button", { name: /how i got this/i })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/SELECT region/)).not.toBeInTheDocument();
  });

  it("shows the tool, its SQL in a copyable block and the rows it returned", async () => {
    await open([SQL]);
    expect(screen.getByText("Ran a SQL query")).toBeInTheDocument();
    const code = screen.getByText(/SELECT region/).closest("pre");
    expect(code).toHaveAttribute("dir", "ltr");
    expect(code).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "North" })).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 rows")).toBeInTheDocument();
  });

  it("limits a long result and still says how many there are", async () => {
    const rows = Array.from({ length: 1000 }, (_, i) => [`item ${i}`, i]);
    await open([step({ evidence: { sql: "SELECT 1", columns: ["a", "b"], rows, row_count: 1000, truncated: true } })]);
    expect(screen.getByText("Showing 50 of 1,000 rows")).toBeInTheDocument();
    expect(screen.getByText("The result stopped at the 1,000 row limit.")).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBeLessThanOrEqual(51);
  });

  it("says when a tool failed, with the reason", async () => {
    await open([step({ ok: false, summary: "Blocked", evidence: { error: "x", message: "Personal data is blocked." } })]);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Personal data is blocked.")).toBeInTheDocument();
  });

  it("marks a tool a stopped answer never finished", async () => {
    await open([step({ ok: null, summary: null })]);
    expect(screen.getByText("Not finished")).toBeInTheDocument();
  });

  it("links findings back to the insight drawer", async () => {
    const onOpen = vi.fn();
    await open(
      [
        step({
          name: "get_insights",
          summary: "1 insights",
          evidence: { insights: [{ insight_id: "i1", title: "Net revenue was 500", type: "kpi_summary" }] },
        }),
      ],
      onOpen,
    );
    await userEvent.click(screen.getByRole("button", { name: "Net revenue was 500" }));
    expect(onOpen).toHaveBeenCalledWith("i1");
  });

  it("shows Python code and its output as LTR text", async () => {
    await open([step({ name: "run_python", evidence: { code: "print(1)", stdout: "1\n" } })]);
    expect(screen.getByText("print(1)").closest("pre")).toHaveAttribute("dir", "ltr");
    expect(screen.getByText("Output")).toBeInTheDocument();
  });

  it("gives an unknown tool its own name", async () => {
    await open([step({ name: "brand_new_tool" })]);
    expect(screen.getByText("brand_new_tool")).toBeInTheDocument();
  });

  it("names the steps in Arabic", async () => {
    renderWithProviders(<HowIGotThis tools={[SQL]} onOpenInsight={vi.fn()} />, { locale: "ar" });
    const button = screen.getByRole("button", { name: /كيف وصلت إلى هذا/ });
    await userEvent.click(button);
    expect(within(button.parentElement as HTMLElement).getByText("تشغيل استعلام SQL")).toBeInTheDocument();
  });

  it("says so when no tool was used", () => {
    renderWithProviders(<HowIGotThis tools={[]} onOpenInsight={vi.fn()} />);
    expect(screen.getByText("This answer used no data tools.")).toBeInTheDocument();
  });
});
