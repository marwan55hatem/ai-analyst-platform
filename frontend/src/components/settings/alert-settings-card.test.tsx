import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { AlertSettingsCard } from "./alert-settings-card";

const SETTINGS = {
  revenue_drop_pct: 15,
  orders_drop_pct: 15,
  churn_increase_pct: 20,
  quality_drop_points: 5,
  freshness_enabled: false,
  freshness_days: 14,
  is_default: true,
  can_edit: true,
  updated_at: null,
};

function backend(over: Partial<typeof SETTINGS>) {
  server.use(http.get("*/api/v1/alert-settings", () => HttpResponse.json({ ...SETTINGS, ...over })));
}

describe("AlertSettingsCard", () => {
  it("lets an owner or admin change the thresholds", async () => {
    backend({});
    let body: unknown = null;
    server.use(
      http.put("*/api/v1/alert-settings", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...SETTINGS, ...(body as object), is_default: false });
      }),
    );
    renderWithProviders(<AlertSettingsCard />);
    const revenue = await screen.findByLabelText("Net revenue drop (%)");
    expect(screen.getByText("Using the default thresholds.")).toBeInTheDocument();
    expect(screen.getByLabelText("Days without new data (days)")).toBeDisabled();
    await userEvent.clear(revenue);
    await userEvent.type(revenue, "10");
    await userEvent.click(screen.getByRole("checkbox", { name: "Alert when no new data arrives" }));
    await userEvent.click(screen.getByRole("button", { name: "Save thresholds" }));
    await waitFor(() =>
      expect(body).toEqual({
        revenue_drop_pct: 10,
        orders_drop_pct: 15,
        churn_increase_pct: 20,
        quality_drop_points: 5,
        freshness_days: 14,
        freshness_enabled: true,
      }),
    );
    expect(revenue).toHaveAttribute("min", "1");
    expect(revenue).toHaveAttribute("max", "90");
  });

  it("is read-only for members", async () => {
    backend({ can_edit: false, freshness_enabled: true, freshness_days: 7 });
    renderWithProviders(<AlertSettingsCard />);
    expect(await screen.findByTestId("alert-settings-readonly")).toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save thresholds" })).toBeNull();
    expect(screen.getByText("Only owners and admins can change these.")).toBeInTheDocument();
    expect(screen.getByText("On · 7 days")).toBeInTheDocument();
  });

  it("is read-only for members in Arabic too", async () => {
    backend({ can_edit: false });
    renderWithProviders(<AlertSettingsCard />, { locale: "ar" });
    expect(await screen.findByText("يستطيع المالكون والمسؤولون فقط تغييرها.")).toBeInTheDocument();
    expect(screen.getByText("متوقف")).toBeInTheDocument();
  });

  it("shows the server's validation error", async () => {
    backend({});
    server.use(
      http.put("*/api/v1/alert-settings", () =>
        HttpResponse.json({ code: "validation_error", message: "bad", details: [] }, { status: 422 }),
      ),
    );
    renderWithProviders(<AlertSettingsCard />, { locale: "ar" });
    await userEvent.click(await screen.findByRole("button", { name: "حفظ الحدود" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
