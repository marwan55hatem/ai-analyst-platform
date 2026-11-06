import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { keys } from "@/lib/queries";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { AlertsBell } from "./alerts-bell";

function unread(count: number) {
  server.use(http.get("*/api/v1/alerts/unread-count", () => HttpResponse.json({ count })));
}

describe("AlertsBell", () => {
  it("names the bell with the count and links to the alerts tab", async () => {
    unread(3);
    renderWithProviders(<AlertsBell />);
    const bell = await screen.findByRole("link", { name: "Alerts, 3 unread" });
    expect(bell).toHaveAttribute("href", "/reports?tab=alerts");
    expect(screen.getByTestId("alerts-bell-count")).toHaveTextContent("3");
  });

  it("has no badge and a plain name with nothing unread", async () => {
    unread(0);
    renderWithProviders(<AlertsBell />);
    expect(await screen.findByRole("link", { name: "Alerts, none unread" })).toBeInTheDocument();
    expect(screen.queryByTestId("alerts-bell-count")).toBeNull();
  });

  it("announces only new alerts, not the first load or an unchanged poll", async () => {
    unread(2);
    const { client } = renderWithProviders(<AlertsBell />);
    await screen.findByRole("link", { name: "Alerts, 2 unread" });
    expect(screen.getByTestId("alerts-announcer")).toHaveTextContent("");
    await client.refetchQueries({ queryKey: keys.unread });
    expect(screen.getByTestId("alerts-announcer")).toHaveTextContent("");
    unread(5);
    await client.refetchQueries({ queryKey: keys.unread });
    await waitFor(() => expect(screen.getByTestId("alerts-announcer")).toHaveTextContent("3 new alerts"));
    expect(screen.getByTestId("alerts-announcer")).toHaveAttribute("aria-live", "polite");
  });

  it("speaks Arabic", async () => {
    unread(1);
    renderWithProviders(<AlertsBell />, { locale: "ar" });
    expect(await screen.findByRole("link", { name: "التنبيهات، واحد غير مقروء" })).toBeInTheDocument();
  });
});
