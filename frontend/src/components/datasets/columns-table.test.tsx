import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { ColumnProfile } from "@/lib/api/types";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { ColumnsTable } from "./columns-table";

const COLUMNS: ColumnProfile[] = [
  {
    name: "unit_price",
    original_name: "Unit Price ($)",
    inferred_type: "float",
    semantic_role: "price",
    role_confidence: 0.9,
    role_source: "rule",
    pii_flag: false,
    stats: {},
  },
  {
    name: "email",
    original_name: "Email",
    inferred_type: "text",
    semantic_role: "email",
    role_confidence: 1,
    role_source: "rule",
    pii_flag: true,
    stats: {},
  },
];

describe("<ColumnsTable>", () => {
  it("renders untrusted column names as text and flags personal data", () => {
    const evil = { ...COLUMNS[0]!, name: "x", original_name: "<img src=x onerror=alert(1)>" };
    renderWithProviders(<ColumnsTable datasetId="d1" version={1} columns={[evil, COLUMNS[1]!]} onRoleChanged={() => {}} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByRole("button", { name: /Personal data/ })).toBeInTheDocument();
  });

  it("saves a new role through the API and asks to re-run the analysis", async () => {
    let sent: unknown = null;
    server.use(
      http.put("*/api/v1/datasets/d1/columns/unit_price/role", async ({ request }) => {
        sent = { body: await request.json(), version: new URL(request.url).searchParams.get("version") };
        return HttpResponse.json({ ...COLUMNS[0], semantic_role: "revenue", role_source: "user" });
      }),
    );
    const onRoleChanged = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ColumnsTable datasetId="d1" version={3} columns={COLUMNS} onRoleChanged={onRoleChanged} />);
    await user.click(screen.getByRole("combobox", { name: "Role for Unit Price ($)" }));
    await user.click(await screen.findByRole("option", { name: "Revenue" }));
    await vi.waitFor(() => expect(onRoleChanged).toHaveBeenCalledTimes(1));
    expect(sent).toEqual({ body: { role: "revenue" }, version: "3" });
  });
});
