import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { DatasetDetail, VersionCompatibility, VersionSummary } from "@/lib/api/types";
import { MAX_UPLOAD_BYTES } from "@/lib/config";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

import { NewVersionDialog } from "./new-version-dialog";
import { VersionList } from "./version-list";
import { VersionRefreshPanel } from "./version-refresh-panel";

const ID = "d1";

function fileOf(name: string, size: number): File {
  const file = new File(["x"], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function summary(over: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id: "v-2",
    version: 2,
    created_at: "2026-11-06T10:00:00Z",
    source: "upload",
    original_filename: "sales.csv",
    row_count: 120,
    quality_score: 91,
    status: "ready",
    profile_status: "ready",
    auto_analyze: true,
    is_current: true,
    analysis_status: "completed",
    alerts_raised: 0,
    ...over,
  };
}

function compat(over: Partial<VersionCompatibility> = {}): VersionCompatibility {
  return {
    version: 2,
    previous_version: 1,
    status: "compatible",
    added: [],
    missing: [],
    changed: [],
    missing_roles: [],
    shorter_period: false,
    previous_rows: 100,
    new_rows: 120,
    previous_first_date: "2024-01-01",
    new_first_date: "2024-01-01",
    blocking: false,
    blocking_reason: null,
    ...over,
  };
}

function serve(opts: { versions: VersionSummary[]; compat: VersionCompatibility; run?: object | null }) {
  server.use(
    http.get("*/api/v1/datasets/:id/versions", () => HttpResponse.json(opts.versions)),
    http.get("*/api/v1/datasets/:id/versions/:n/compatibility", () => HttpResponse.json(opts.compat)),
    http.get("*/api/v1/datasets/:id/analysis-runs", () =>
      HttpResponse.json(opts.run ? [{ id: "r1", version: 2, status: "completed", insight_count: 3 }] : []),
    ),
    http.get("*/api/v1/analysis-runs/:id", () =>
      HttpResponse.json({
        id: "r1",
        status: "completed",
        progress: { total: 1, modules: { kpis: { status: "completed", insights: 3, warnings: [] } } },
      }),
    ),
  );
}

function panel(locale: "en" | "ar" = "en", analyze = true) {
  return renderWithProviders(
    <VersionRefreshPanel
      datasetId={ID}
      version={2}
      analyze={analyze}
      onDismiss={vi.fn()}
      onUploaded={vi.fn()}
    />,
    { locale },
  );
}

describe("<NewVersionDialog>", () => {
  it("opens with the complete-export sentence and the checkbox on by default", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NewVersionDialog datasetId={ID} onUploaded={vi.fn()} />);
    await user.click(screen.getByTestId("new-version-open"));
    expect(await screen.findByRole("dialog", { name: "Upload new data" })).toBeInTheDocument();
    expect(screen.getByTestId("complete-export-note")).toHaveTextContent(/complete export of the same data/);
    expect(screen.getByRole("checkbox", { name: "Analyse automatically" })).toBeChecked();
    expect(screen.getByTestId("new-version-submit")).toBeDisabled();
  });

  it.each([
    ["notes.txt", 20, /"notes.txt" isn't a CSV, TSV or .xlsx file/],
    ["empty.csv", 0, /is empty/],
    ["big.csv", MAX_UPLOAD_BYTES + 5 * 1024 * 1024, /over the 50 MB limit/],
  ])("shows the same error as /datasets/new for %s", async (name, size, message) => {
    const user = userEvent.setup({ applyAccept: false });
    renderWithProviders(<NewVersionDialog datasetId={ID} onUploaded={vi.fn()} />);
    await user.click(screen.getByTestId("new-version-open"));
    await user.upload(await screen.findByTestId("new-version-file"), fileOf(name, size));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByTestId("new-version-submit")).toBeDisabled();
  });

  it("sends analyze=false when the box is cleared, then reports the new version", async () => {
    let sent = "";
    server.use(
      http.post("*/api/v1/datasets/:id/versions", async ({ request }) => {
        sent = await request.text();
        const detail = { id: ID, versions: [{ version: 2 }], latest_version: { version: 2 } };
        return HttpResponse.json(detail, { status: 201 });
      }),
    );
    const onUploaded = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<NewVersionDialog datasetId={ID} onUploaded={onUploaded} />);
    await user.click(screen.getByTestId("new-version-open"));
    await user.upload(await screen.findByTestId("new-version-file"), fileOf("sales.csv", 50));
    await user.click(screen.getByRole("checkbox", { name: "Analyse automatically" }));
    await user.click(screen.getByTestId("new-version-submit"));
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
    expect(sent).toMatch(/name="analyze"\s+false/);
    expect(sent).not.toMatch(/name="name"/);
    const [detail, analyze] = onUploaded.mock.calls[0] as [DatasetDetail, boolean];
    expect(detail.latest_version?.version).toBe(2);
    expect(analyze).toBe(false);
  });

  it("keeps the dialog open with an honest error when the server refuses", async () => {
    server.use(
      http.post("*/api/v1/datasets/:id/versions", () =>
        HttpResponse.json({ code: "unsupported_media_type", message: "x", details: {} }, { status: 415 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<NewVersionDialog datasetId={ID} onUploaded={vi.fn()} />);
    await user.click(screen.getByTestId("new-version-open"));
    await user.upload(await screen.findByTestId("new-version-file"), fileOf("sales.csv", 50));
    await user.click(screen.getByTestId("new-version-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't supported/);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("new-version-submit")).toBeEnabled();
  });

  it("speaks Arabic", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NewVersionDialog datasetId={ID} onUploaded={vi.fn()} />, { locale: "ar" });
    await user.click(screen.getByTestId("new-version-open"));
    expect(await screen.findByRole("dialog", { name: "رفع بيانات جديدة" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "حلّل تلقائيًا" })).toBeChecked();
  });
});

describe("<VersionRefreshPanel>", () => {
  it("says the version is ready and that nothing raised an alert", async () => {
    serve({ versions: [summary()], compat: compat(), run: {} });
    panel();
    expect(await screen.findByRole("heading", { name: "Version 2 is ready" }, { timeout: 8000 })).toBeInTheDocument();
    expect(await screen.findByTestId("refresh-no-alerts")).toHaveTextContent(/No new alerts/);
    expect(screen.getByTestId("refresh-dashboard")).toHaveAttribute("href", `/dashboard?dataset=${ID}`);
    expect(screen.getByTestId("compat")).toHaveTextContent(/Same columns and types as version 1/);
  }, 15000);

  it("links to the alerts tab with the count when the run raised alerts", async () => {
    serve({ versions: [summary({ alerts_raised: 2 })], compat: compat(), run: {} });
    panel();
    const link = await screen.findByTestId("refresh-alerts-link", {}, { timeout: 8000 });
    expect(link).toHaveTextContent("2 new alerts");
    expect(link).toHaveAttribute("href", "/reports?tab=alerts");
  }, 15000);

  it("names added, missing and changed columns and the lost roles in plain words", async () => {
    serve({
      versions: [summary({ analysis_status: null })],
      compat: compat({
        status: "warnings",
        added: ["channel"],
        missing: ["revenue"],
        changed: [
          { name: "region", previous_type: "categorical", new_type: "integer", previous_role: "region", new_role: "other" },
        ],
        missing_roles: ["revenue"],
      }),
      run: null,
    });
    panel("en", false);
    const box = await screen.findByTestId("compat");
    expect(box).toHaveTextContent("1 new column: channel. New columns are fine.");
    expect(box).toHaveTextContent("1 column is missing compared with the previous version: revenue.");
    expect(box).toHaveTextContent("region: categorical to integer");
    expect(box).toHaveTextContent("The file no longer has a revenue column");
    expect(await screen.findByTestId("refresh-outcome")).toHaveTextContent(/The analysis was not run/);
  });

  it("warns about a shorter period without blocking", async () => {
    serve({
      versions: [summary()],
      compat: compat({ status: "warnings", shorter_period: true }),
      run: {},
    });
    panel();
    expect(await screen.findByTestId("compat-shorter")).toHaveTextContent(
      "This file covers a shorter period than the previous version. If it only contains new rows, upload a complete export instead, because this upload replaces the previous data.",
    );
    expect(screen.queryByTestId("refresh-blocked")).toBeNull();
  });

  it("blocks with the reason and a next action", async () => {
    serve({
      versions: [summary({ status: "ready", analysis_status: null })],
      compat: compat({
        status: "blocked",
        blocking: true,
        blocking_reason: "The file has no date column and no revenue or quantity column.",
      }),
      run: null,
    });
    panel();
    const blocked = await screen.findByTestId("refresh-blocked");
    expect(blocked).toHaveTextContent(/no date column/);
    expect(blocked).toHaveTextContent(/Upload a corrected file/);
    expect(screen.getByRole("heading", { name: "This file can't be analysed" })).toBeInTheDocument();
  });

  it("speaks Arabic for the shorter-period warning and the outcome", async () => {
    serve({
      versions: [summary({ alerts_raised: 1 })],
      compat: compat({ status: "warnings", shorter_period: true }),
      run: {},
    });
    panel("ar");
    expect(await screen.findByTestId("compat-shorter")).toHaveTextContent(
      "هذا الملف يغطي فترة أقصر من النسخة السابقة. إذا كان يحتوي على الصفوف الجديدة فقط، فارفع تصديرًا كاملًا بدلًا منه، لأن هذا الرفع يحل محل البيانات السابقة.",
    );
    expect(await screen.findByRole("heading", { name: "النسخة 2 جاهزة" }, { timeout: 8000 })).toBeInTheDocument();
    expect(await screen.findByTestId("refresh-alerts-link")).toHaveTextContent("تنبيه جديد واحد");
  }, 15000);
});

describe("<VersionList>", () => {
  const versions = [
    summary({ alerts_raised: 1 }),
    summary({
      id: "v-1",
      version: 1,
      source: "cleaning",
      is_current: false,
      row_count: null,
      quality_score: null,
      analysis_status: null,
    }),
  ];

  it("shows version, date, source, rows, quality and analysis status", async () => {
    server.use(http.get("*/api/v1/datasets/:id/versions", () => HttpResponse.json(versions)));
    renderWithProviders(
      <VersionList datasetId={ID} selected={2} hrefFor={(n) => `/datasets/${ID}?v=${n}`} />,
    );
    const rows = await screen.findAllByTestId("version-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Version 2");
    expect(rows[0]).toHaveTextContent("Current");
    expect(rows[0]).toHaveTextContent("Upload");
    expect(rows[0]).toHaveTextContent("120");
    expect(rows[0]).toHaveTextContent("91");
    expect(rows[0]).toHaveTextContent("Done");
    expect(rows[0]).toHaveTextContent("1 alert");
    expect(rows[1]).toHaveTextContent("Cleaning");
    expect(rows[1]).toHaveTextContent("Not analysed");
    expect(screen.getByRole("link", { name: "Version 1" })).toHaveAttribute("href", `/datasets/${ID}?v=1`);
  });

  it("renders in Arabic", async () => {
    server.use(http.get("*/api/v1/datasets/:id/versions", () => HttpResponse.json(versions)));
    renderWithProviders(
      <VersionList datasetId={ID} selected={2} hrefFor={(n) => `/datasets/${ID}?v=${n}`} />,
      { locale: "ar" },
    );
    const rows = await screen.findAllByTestId("version-row");
    expect(rows[1]).toHaveTextContent("تنظيف");
    expect(rows[1]).toHaveTextContent("لم يُحلَّل");
  });

  it("shows an error with a retry when the history cannot load", async () => {
    server.use(http.get("*/api/v1/datasets/:id/versions", () => HttpResponse.json({}, { status: 500 })));
    renderWithProviders(
      <VersionList datasetId={ID} selected={2} hrefFor={(n) => `/datasets/${ID}?v=${n}`} />,
    );
    expect(await screen.findByText("We couldn't load the version history.")).toBeInTheDocument();
  });
});
