import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Dataset, Report } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { AlertsFeed, alertHref } from "./alerts-feed";
import { GenerateDialog } from "./generate-dialog";
import { ReportList } from "./report-list";

const BASE: Report = {
  id: "r1",
  dataset_id: "d1",
  title: "Sales",
  version: 2,
  run_id: "run1",
  locale: "en",
  status: "completed",
  progress: 100,
  size_bytes: 180_000,
  error_code: null,
  created_by: "u1",
  created_at: "2026-11-04T09:00:00Z",
  finished_at: "2026-11-04T09:00:20Z",
};

function reports(items: Partial<Report>[]) {
  server.use(
    http.get("*/api/v1/reports", () =>
      HttpResponse.json({ items: items.map((r, i) => ({ ...BASE, id: `r${i}`, ...r })), total: items.length }),
    ),
  );
}

describe("ReportList", () => {
  it("has an honest empty state", async () => {
    reports([]);
    renderWithProviders(<ReportList />);
    expect(await screen.findByText("No reports yet")).toBeInTheDocument();
  });

  it("lists status, version, language and size, with named actions", async () => {
    reports([{}, { status: "running", progress: 40, locale: "ar", size_bytes: null }]);
    renderWithProviders(<ReportList />);
    const rows = await screen.findAllByTestId("report-row");
    expect(within(rows[0]!).getByText("Ready")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Version 2")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("175.8 KB")).toBeInTheDocument();
    expect(
      within(rows[0]!).getByRole("button", { name: "Download the English report of Sales, version 2" }),
    ).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Generating")).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("progressbar", { name: "Generating, 40% done" })).toBeInTheDocument();
    expect(within(rows[1]!).queryByRole("button", { name: /Download/ })).toBeNull();
    expect(within(rows[1]!).queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("explains a failure with the next action", async () => {
    reports([{ status: "failed", error_code: "no_analysis" }, { status: "failed", error_code: "weird" }]);
    renderWithProviders(<ReportList />);
    const failed = await screen.findAllByTestId("report-failed");
    expect(failed[0]!).toHaveTextContent("Run the analysis again");
    expect(failed[1]!).toHaveTextContent("Something went wrong");
  });

  it("asks before deleting and deletes", async () => {
    reports([{}]);
    let deleted = "";
    server.use(
      http.delete("*/api/v1/reports/:id", ({ params }) => {
        deleted = String(params.id);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<ReportList />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete the English report of Sales, version 2" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete this report?" });
    expect(dialog).toHaveTextContent("cannot be undone");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete report" }));
    await waitFor(() => expect(deleted).toBe("r0"));
  });

  it("speaks Arabic", async () => {
    reports([{ locale: "ar" }]);
    renderWithProviders(<ReportList />, { locale: "ar" });
    expect(await screen.findByText("جاهز")).toBeInTheDocument();
    expect(screen.getByText("العربية")).toBeInTheDocument();
  });
});

const DATASET = { id: "d1", name: "Sales" } as Dataset;

describe("GenerateDialog", () => {
  it("says what is inside and sends the language", async () => {
    let body: unknown = null;
    server.use(
      http.post("*/api/v1/datasets/d1/reports", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...BASE, status: "queued" }, { status: 202 });
      }),
      http.get("*/api/v1/reports", () => HttpResponse.json({ items: [], total: 0 })),
    );
    renderWithProviders(<GenerateDialog dataset={DATASET} version={3} defaultLocale="en" />);
    await userEvent.click(screen.getByRole("button", { name: "Generate report" }));
    const dialog = await screen.findByRole("dialog", { name: "Generate a report" });
    expect(dialog).toHaveTextContent("No AI model writes or computes anything");
    expect(dialog).toHaveTextContent("What-if scenarios are not included.");
    expect(dialog).toHaveTextContent("no customer is named");
    expect(dialog).toHaveTextContent("Version 3");
    await userEvent.click(within(dialog).getByRole("radio", { name: "Arabic" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(body).toEqual({ version: 3, locale: "ar" }));
  });

  it("shows why the server refused", async () => {
    server.use(
      http.post("*/api/v1/datasets/d1/reports", () =>
        HttpResponse.json(
          { code: "report_busy", message: "busy", details: { retry_after_seconds: 10 } },
          { status: 429, headers: { "Retry-After": "10" } },
        ),
      ),
    );
    renderWithProviders(<GenerateDialog dataset={DATASET} version={1} defaultLocale="ar" />, { locale: "ar" });
    await userEvent.click(screen.getByRole("button", { name: "إنشاء تقرير" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: "العربية" })).toBeChecked();
    await userEvent.click(within(dialog).getByRole("button", { name: "إنشاء" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("يجري إنشاء تقرير بالفعل");
  });
});

const ALERT = {
  id: "a1",
  dataset_id: "d1",
  run_id: "run2",
  rule: "revenue_drop",
  severity: "critical",
  title: "stored title",
  body: "stored body",
  payload: {
    metric: "net_revenue",
    method: "forecast",
    period: "2025-01-01",
    current: 56000,
    expected: 140000,
    range_level: 95,
    range_low: 75000,
    range_high: 204000,
    forecast_made_through: "2024-12-01",
    link: { page: "dashboard", dataset_id: "d1" },
  },
  created_at: "2026-11-04T09:00:00Z",
  read_at: null,
};

function alerts(items: object[]) {
  server.use(
    http.get("*/api/v1/alerts", ({ request }) => {
      const unread = new URL(request.url).searchParams.get("unread") === "true";
      const shown = unread ? items.filter((a) => (a as { read_at: unknown }).read_at === null) : items;
      return HttpResponse.json({ items: shown, total: shown.length });
    }),
  );
}

describe("AlertsFeed", () => {
  it("shows severity as icon and word, the compared numbers and a link", async () => {
    alerts([
      ALERT,
      {
        ...ALERT,
        id: "a2",
        rule: "orders_drop",
        severity: "warning",
        read_at: "2026-11-04T10:00:00Z",
        payload: {
          metric: "orders",
          method: "month_over_month",
          period: "2025-01-01",
          previous_period: "2024-12-01",
          current: 1200,
          previous: 2000,
          change_pct: -40,
          link: { page: "dashboard", dataset_id: "d1" },
        },
      },
    ]);
    renderWithProviders(<AlertsFeed />);
    const items = await screen.findAllByTestId("alert-item");
    expect(within(items[0]!).getByText("Critical")).toBeInTheDocument();
    expect(within(items[0]!).getByRole("heading")).toHaveTextContent("Net revenue in Jan 2025 was below the forecast range");
    expect(items[0]!).toHaveTextContent("56,000");
    expect(items[0]!).toHaveTextContent("95% range of the forecast (75,000 to 204,000; expected 140,000)");
    expect(within(items[0]!).getByRole("link", { name: "Open dashboard" })).toHaveAttribute(
      "href",
      "/dashboard?dataset=d1",
    );
    expect(items[1]!).toHaveTextContent("Orders in Jan 2025 fell 40% from Dec 2024");
    expect(items[1]!).toHaveTextContent("Seasonality is not accounted for.");
    expect(within(items[1]!).queryByRole("button", { name: /Mark read/ })).toBeNull();
  });

  it("marks one and all read", async () => {
    alerts([ALERT]);
    const hits: string[] = [];
    server.use(
      http.post("*/api/v1/alerts/:id/read", ({ params }) => {
        hits.push(`one:${String(params.id)}`);
        return HttpResponse.json({ ...ALERT, read_at: "2026-11-04T11:00:00Z" });
      }),
      http.post("*/api/v1/alerts/read-all", () => {
        hits.push("all");
        return HttpResponse.json({ updated: 1 });
      }),
    );
    renderWithProviders(<AlertsFeed />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Mark read: Net revenue in Jan 2025 was below the forecast range" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(hits).toEqual(["one:a1", "all"]));
  });

  it("has an empty state, also for unread only", async () => {
    alerts([{ ...ALERT, read_at: "2026-11-04T11:00:00Z" }]);
    renderWithProviders(<AlertsFeed />);
    await screen.findAllByTestId("alert-item");
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Unread only" }));
    expect(await screen.findByText("No unread alerts")).toBeInTheDocument();
  });

  it("falls back to the stored English text when the payload is incomplete", async () => {
    alerts([{ ...ALERT, payload: {} }]);
    renderWithProviders(<AlertsFeed />, { locale: "ar" });
    const heading = within((await screen.findAllByTestId("alert-item"))[0]!).getByRole("heading");
    expect(heading).toHaveTextContent("stored title");
    expect(heading).toHaveAttribute("lang", "en");
    expect(screen.getByText("حرج")).toBeInTheDocument();
  });

  it("writes Arabic sentences from the numbers", async () => {
    alerts([ALERT]);
    renderWithProviders(<AlertsFeed />, { locale: "ar" });
    const item = (await screen.findAllByTestId("alert-item"))[0]!;
    expect(item).toHaveTextContent("كان صافي الإيرادات في");
    expect(item).toHaveTextContent("56,000");
    await act(async () => undefined);
  });

  it("links each rule to its page", () => {
    expect(alertHref({ page: "churn", dataset_id: "d" }, "x")).toBe("/insights/churn?dataset=d");
    expect(alertHref({ page: "insights", dataset_id: "d" }, "x")).toBe("/insights?dataset=d");
    expect(alertHref({ page: "dataset", dataset_id: "d" }, "x")).toBe("/datasets/d");
    expect(alertHref(undefined, "x")).toBe("/dashboard?dataset=x");
  });
});
