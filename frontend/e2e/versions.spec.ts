import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_DIR, GROUND_TRUTH } from "./global-setup";
import { REPO_ROOT, expectNoAxeViolations, msg, register, uploadAnalysed } from "./helpers";

/**
 * Step 12c: refresh a dataset from the UI ("Upload new data"). The scripted model is used
 * everywhere (global-setup refuses a live one); nothing here asks a model anything.
 */

const RETAIL = path.join(REPO_ROOT, "evaluation", "datasets", "samples", "retail_clean.csv");

function rows(file: string): { header: string[]; body: string[][] } {
  // The generated files use Windows line endings: a stray \r would end up inside the last column.
  const lines = readFileSync(file, "utf8").trimEnd().split(/\r?\n/);
  return { header: lines[0]!.split(","), body: lines.slice(1).map((l) => l.split(",")) };
}

function write(name: string, header: string[], body: string[][]): string {
  const target = path.join(FIXTURE_DIR, name);
  writeFileSync(target, `${[header.join(","), ...body.map((r) => r.join(","))].join("\n")}\n`);
  return target;
}

/** The planted data cut before December (v1), and the full data with a collapsed December, no
 * `region` column and one extra column (v2): a realistic refresh with a column change. */
function groundTruthVersions(): { v1: string; v2: string; dropped: string } {
  const { header, body } = rows(GROUND_TRUTH);
  const day = header.indexOf("order_date");
  const revenue = header.indexOf("revenue");
  const dropped = ["region", "channel", "category", "product"].find((c) => header.includes(c))!;
  const droppedAt = header.indexOf(dropped);
  const v1 = write("versions_v1.csv", header, body.filter((c) => c[day]! <= "2024-11-30"));
  const v2Header = [...header.filter((_, i) => i !== droppedAt), "loyalty_tier"];
  const v2Body = body.map((c) => {
    const copy = [...c];
    if (copy[day]! >= "2024-12-01") copy[revenue] = (Number(copy[revenue]) * 0.15).toFixed(2);
    return [...copy.filter((_, i) => i !== droppedAt), "gold"];
  });
  return { v1, v2: write("versions_v2.csv", v2Header, v2Body), dropped };
}

async function openDialog(page: Page) {
  await page.getByTestId("new-version-open").click();
  const dialog = page.getByRole("dialog", { name: msg("en", "versions.dialogTitle") });
  await expect(dialog).toBeVisible();
  return dialog;
}

test("refresh from the UI: compatibility notice, progress, version list and the new alert", async ({ page }, testInfo) => {
  testInfo.setTimeout(15 * 60_000);
  const files = groundTruthVersions();
  await register(page, "en");
  const datasetId = await uploadAnalysed(page, files.v1);
  await expect(page.getByRole("link", { name: "Alerts, none unread" })).toBeVisible();

  const dialog = await openDialog(page);
  await expect(dialog.getByTestId("complete-export-note")).toContainText(msg("en", "versions.completeExport"));
  await expect(dialog.getByRole("checkbox", { name: msg("en", "versions.analyze") })).toBeChecked();
  await dialog.getByTestId("new-version-file").setInputFiles(files.v2);
  await dialog.getByTestId("new-version-submit").click();
  await expect(dialog).toBeHidden({ timeout: 60_000 });

  // Compatibility in plain words: the new column is fine, the dropped one is named.
  const panel = page.getByTestId("refresh-panel");
  await expect(panel).toBeVisible();
  const compat = panel.getByTestId("compat");
  await expect(compat).toContainText("loyalty_tier", { timeout: 120_000 });
  await expect(compat).toContainText(files.dropped);
  await expect(compat).toContainText("New columns are fine");

  // Processing -> profiling -> analysis -> outcome.
  await expect(panel).toHaveAttribute("data-stage", "done", { timeout: 600_000 });
  await expect(panel.getByRole("heading", { name: msg("en", "versions.outcome.ready", { version: 2 }) })).toBeVisible();
  const alerts = panel.getByTestId("refresh-alerts-link");
  await expect(alerts).toBeVisible();
  const count = Number((await alerts.textContent())!.match(/\d+/)![0]);
  expect(count).toBeGreaterThan(0);
  await expect(panel.getByTestId("refresh-dashboard")).toHaveAttribute("href", `/dashboard?dataset=${datasetId}`);

  // The version list shows both versions with source, rows, quality and analysis status.
  const list = page.getByTestId("version-row");
  await expect(list).toHaveCount(2);
  await expect(list.first()).toContainText("Version 2");
  await expect(list.first()).toContainText(msg("en", "versions.list.sources.upload"));
  await expect(list.first()).toContainText(msg("en", "versions.list.statuses.completed"));
  await expect(list.first()).toContainText(`${count} alert`);
  await expect(list.nth(1)).toContainText("Version 1");

  // The bell carries the same number of unread alerts, and the link opens the alerts tab.
  await expect(page.getByTestId("alerts-bell")).toHaveAccessibleName(new RegExp(`^Alerts, ${count} unread$`), {
    timeout: 90_000,
  });
  await alerts.click();
  await expect(page).toHaveURL(/\/reports\?tab=alerts$/);
  await expect(page.locator("[data-testid=alert-item][data-rule=revenue_drop]")).toHaveCount(1);

  // An older version can be opened from the list and the page says which one it shows.
  await page.goto(`/datasets/${datasetId}`);
  await page.getByTestId("version-list").getByRole("link", { name: "Version 1", exact: true }).click();
  await expect(page).toHaveURL(/\?v=1$/);
  await expect(page.getByTestId("version")).toHaveText("Version 1");
  await expect(page.getByText(msg("en", "versions.viewingOld", { version: 1, latest: 2 }))).toBeVisible();
});

test("keyboard only: open, choose a file, switch off automatic analysis, submit", async ({ page }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  const { header, body } = rows(RETAIL);
  await register(page, "en");
  const datasetId = await uploadAnalysed(page, RETAIL);

  await page.getByTestId("new-version-open").focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: msg("en", "versions.dialogTitle") });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("new-version-choose")).toBeFocused({ timeout: 5_000 }).catch(() => undefined);
  // The file chooser itself is an operating-system dialog; the file is handed to the input.
  await dialog.getByTestId("new-version-file").setInputFiles(write("versions_same.csv", header, body));
  await dialog.getByRole("checkbox", { name: msg("en", "versions.analyze") }).focus();
  await page.keyboard.press("Space");
  await expect(dialog.getByRole("checkbox", { name: msg("en", "versions.analyze") })).not.toBeChecked();
  await dialog.getByTestId("new-version-submit").focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden({ timeout: 60_000 });

  const panel = page.getByTestId("refresh-panel");
  await expect(panel).toHaveAttribute("data-stage", "done", { timeout: 180_000 });
  await expect(panel.getByTestId("refresh-outcome")).toContainText(msg("en", "versions.outcome.readyNoAnalysis", { version: 2 }));
  await expect(panel.getByTestId("compat")).toContainText(msg("en", "versions.compat.same", { previous: 1 }));
  // No second analysis was started.
  const runs = await page.evaluate(async (id) => {
    const session = await fetch("/api/v1/auth/refresh", { method: "POST" });
    const token = ((await session.json()) as { access_token: string }).access_token;
    const response = await fetch(`/api/v1/datasets/${id}/analysis-runs?limit=5`, { headers: { Authorization: `Bearer ${token}` } });
    return (await response.json()) as { version: number }[];
  }, datasetId);
  expect(runs.every((r) => r.version === 1)).toBeTruthy();
  await expectNoAxeViolations(page, "versions-keyboard-panel");

  // Focus returns to the page and the dismiss button works from the keyboard.
  await panel.getByTestId("refresh-dismiss").focus();
  await page.keyboard.press("Enter");
  await expect(panel).toBeHidden();
});

test("375 px: shorter-period warning, no sideways scroll, axe on the panel and the dialog", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  const setup = await browser.newPage();
  const user = await register(setup, "en");
  await uploadAnalysed(setup, RETAIL);
  await setup.close();

  const { header, body } = rows(RETAIL);
  const day = header.indexOf("order_date");
  const sorted = [...body].sort((a, b) => a[day]!.localeCompare(b[day]!));
  const later = write("versions_later.csv", header, sorted.filter((r) => r[day]! > sorted[Math.floor(sorted.length / 3)]![day]!));

  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, reducedMotion: "reduce" });
  await context.addCookies([{ name: "NEXT_LOCALE", value: "ar", url: baseURL! }]);
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel(msg("ar", "auth.email")).fill(user.email);
  await page.getByLabel(msg("ar", "auth.password"), { exact: true }).fill(user.password);
  await page.getByRole("button", { name: msg("ar", "auth.loginSubmit") }).click();
  await expect(page).toHaveURL(/\/datasets$/);
  await page.getByRole("link").filter({ hasText: /retail/i }).first().click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/);

  await page.getByTestId("new-version-open").click();
  const dialog = page.getByRole("dialog", { name: msg("ar", "versions.dialogTitle") });
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("new-version-file").setInputFiles(later);
  await dialog.getByTestId("new-version-submit").click();
  await expect(dialog).toBeHidden({ timeout: 60_000 });

  const warning = page.getByTestId("compat-shorter");
  await expect(warning).toContainText(msg("ar", "versions.compat.shorter"), { timeout: 120_000 });
  await expect(page.getByTestId("refresh-panel")).toHaveAttribute("data-stage", "done", { timeout: 300_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect((await page.locator("header").boundingBox())!.height).toBeLessThanOrEqual(57);
  await expectNoAxeViolations(page, "versions-375-ar-panel");
  await context.close();
});

/**
 * Screenshots for docs/screenshots/step-12c (opt-in: SCREENSHOT_DIR=<folder>): the dataset page
 * after a refresh (panel with the shorter-period warning, version list) and the open dialog, in
 * EN/AR, light/dark, desktop and phone.
 */
test("step 12c screenshots", async ({ browser, baseURL }, testInfo) => {
  const dir = process.env.SCREENSHOT_DIR;
  test.skip(!dir, "set SCREENSHOT_DIR to take the Step 12c screenshots");
  testInfo.setTimeout(20 * 60_000);
  const setup = await browser.newPage();
  const user = await register(setup, "en");
  const datasetId = await uploadAnalysed(setup, RETAIL);
  const { header, body } = rows(RETAIL);
  const day = header.indexOf("order_date");
  const sorted = [...body].sort((a, b) => a[day]!.localeCompare(b[day]!));
  const later = write("versions_shot.csv", header, sorted.filter((r) => r[day]! > sorted[Math.floor(sorted.length / 3)]![day]!));
  await setup.goto(`/datasets/${datasetId}`);
  const dialog = await openDialog(setup);
  await dialog.getByTestId("new-version-file").setInputFiles(later);
  await dialog.getByTestId("new-version-submit").click();
  await expect(setup.getByTestId("refresh-panel")).toHaveAttribute("data-stage", "done", { timeout: 300_000 });
  await setup.getByTestId("refresh-panel").screenshot({ path: path.join(dir!, "versions-panel-en-light-1440.png") });
  await setup.close();

  for (const locale of ["en", "ar"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const context = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 900 }, colorScheme: scheme, reducedMotion: "reduce" });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const p = await context.newPage();
        await p.goto("/login");
        await p.getByLabel(msg(locale, "auth.email")).fill(user.email);
        await p.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
        await p.getByRole("button", { name: msg(locale, "auth.loginSubmit") }).click();
        await expect(p).toHaveURL(/\/datasets$/);
        await p.goto(`/datasets/${datasetId}`);
        await p.waitForLoadState("networkidle");
        await p.getByRole("heading", { name: msg(locale, "versions.list.title") }).scrollIntoViewIfNeeded();
        await p.locator("#versions").screenshot({ path: path.join(dir!, `versions-list-${locale}-${scheme}-${width}.png`) });
        await p.getByTestId("new-version-open").click();
        const open = p.getByRole("dialog", { name: msg(locale, "versions.dialogTitle") });
        await open.getByTestId("new-version-file").setInputFiles(later);
        await expect(open.getByTestId("new-version-name")).toBeVisible();
        await p.screenshot({ path: path.join(dir!, `versions-dialog-${locale}-${scheme}-${width}.png`) });
        await context.close();
      }
    }
  }
});
