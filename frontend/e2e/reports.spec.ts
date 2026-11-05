import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_DIR, GROUND_TRUTH } from "./global-setup";
import { analysedUser, login, msg, register, uploadAnalysed, type DemoUser } from "./helpers";

/**
 * Step 10: PDF reports (English and Arabic, generated and downloaded through the UI, keyboard
 * only for the Arabic one) and alerts after a second analysis that differs from the first.
 * Everything runs on the scripted model; reports never call a model at all.
 */

async function downloadReport(page: Page, row: ReturnType<Page["getByTestId"]>, viaKeyboard = false) {
  const waiting = page.waitForEvent("download");
  const button = row.getByTestId("report-download");
  if (viaKeyboard) {
    await button.focus();
    await page.keyboard.press("Enter");
  } else {
    await button.click();
  }
  const download = await waiting;
  const file = await download.path();
  const head = readFileSync(file).subarray(0, 5).toString("latin1");
  return { name: download.suggestedFilename(), head };
}

test("generate an English and an Arabic report, download both, delete one", async ({ page, browser }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/reports");
  await expect(page.getByRole("heading", { level: 1, name: msg("en", "reports.title") })).toBeVisible();

  // English, with the mouse.
  await page.getByTestId("report-generate").click();
  const dialog = page.getByRole("dialog", { name: msg("en", "reports.generateTitle") });
  await expect(dialog).toContainText(msg("en", "reports.excludes"));
  await dialog.getByRole("radio", { name: msg("en", "reports.languages.en") }).check();
  await dialog.getByTestId("report-submit").click();
  await expect(dialog).toBeHidden();
  const english = page.getByTestId("report-row").filter({ hasText: msg("en", "reports.languages.en") }).first();
  await expect(english).toHaveAttribute("data-status", "completed", { timeout: 120_000 });
  const en = await downloadReport(page, english);
  expect(en.head).toBe("%PDF-");
  expect(en.name).toMatch(/^report-[a-z0-9-]+-v1-en-\d{8}\.pdf$/);

  // Arabic, keyboard only: open, pick the language with the arrows, submit with Enter.
  await page.getByTestId("report-generate").focus();
  await page.keyboard.press("Enter");
  const keyboardDialog = page.getByRole("dialog", { name: msg("en", "reports.generateTitle") });
  await expect(keyboardDialog).toBeVisible();
  const englishRadio = keyboardDialog.getByRole("radio", { name: msg("en", "reports.languages.en") });
  await englishRadio.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Space");
  await expect(keyboardDialog.getByRole("radio", { name: msg("en", "reports.languages.ar") })).toBeChecked();
  await keyboardDialog.getByTestId("report-submit").focus();
  await page.keyboard.press("Enter");
  await expect(keyboardDialog).toBeHidden();
  const arabic = page.getByTestId("report-row").filter({ hasText: msg("en", "reports.languages.ar") }).first();
  await expect(arabic).toHaveAttribute("data-status", "completed", { timeout: 120_000 });
  const ar = await downloadReport(page, arabic, true);
  expect(ar.head).toBe("%PDF-");
  expect(ar.name).toMatch(/-ar-\d{8}\.pdf$/);

  // Delete the English one after confirming.
  const before = await page.getByTestId("report-row").count();
  await english.getByTestId("report-delete").click();
  await page.getByTestId("report-delete-confirm").click();
  await expect(page.getByTestId("report-row")).toHaveCount(before - 1);
});

/** A copy of the ground truth that ends earlier (v1) and one whose December collapsed (v2). */
function versions(): { v1: string; v2: string } {
  const lines = readFileSync(GROUND_TRUTH, "utf8").trimEnd().split("\n");
  const header = lines[0]!.split(",");
  const day = header.indexOf("order_date");
  const revenue = header.indexOf("revenue");
  const body = lines.slice(1).map((line) => line.split(","));
  const v1 = [lines[0], ...body.filter((c) => c[day]! <= "2024-11-30").map((c) => c.join(","))];
  const v2 = [
    lines[0],
    ...body.map((c) => {
      if (c[day]! >= "2024-12-01") c[revenue] = (Number(c[revenue]) * 0.15).toFixed(2);
      return c.join(",");
    }),
  ];
  const paths = { v1: path.join(FIXTURE_DIR, "alerts_v1.csv"), v2: path.join(FIXTURE_DIR, "alerts_v2.csv") };
  writeFileSync(paths.v1, `${v1.join("\n")}\n`);
  writeFileSync(paths.v2, `${v2.join("\n")}\n`);
  return paths;
}

async function token(page: Page, user: DemoUser): Promise<string> {
  const response = await page.request.post("/api/v1/auth/login", { data: { email: user.email, password: user.password } });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

test("alerts appear after a second analysis that differs; bell count and mark read", async ({ page }, testInfo) => {
  testInfo.setTimeout(15 * 60_000);
  const files = versions();
  const user = await register(page, "en");
  const datasetId = await uploadAnalysed(page, files.v1);

  // The first analysis has nothing to compare with.
  await page.goto("/reports?tab=alerts");
  await expect(page.getByText(msg("en", "alerts.empty.title"))).toBeVisible();
  await expect(page.getByRole("link", { name: "Alerts, none unread" })).toBeVisible();

  // Version 2 through the API (the UI has no "new version" upload yet); its analysis runs itself.
  const bearer = { Authorization: `Bearer ${await token(page, user)}` };
  const upload = await page.request.post(`/api/v1/datasets/${datasetId}/versions`, {
    headers: bearer,
    multipart: { file: { name: "v2.csv", mimeType: "text/csv", buffer: readFileSync(files.v2) } },
  });
  expect(upload.status()).toBe(201);
  await expect
    .poll(
      async () => {
        const runs = await page.request.get(`/api/v1/datasets/${datasetId}/analysis-runs?limit=1`, { headers: bearer });
        const [run] = (await runs.json()) as { version: number; status: string }[];
        return run && run.version === 2 ? run.status : "waiting";
      },
      { timeout: 600_000, intervals: [3_000] },
    )
    .toMatch(/^completed/);

  // The bell shows the new alerts after the next visit; the feed states the compared numbers.
  await page.goto("/datasets");
  const bell = page.getByTestId("alerts-bell");
  await expect(bell).toHaveAccessibleName(/^Alerts, \d+ unread$/);
  const unread = Number((await bell.getAttribute("aria-label"))!.match(/\d+/)![0]);
  expect(unread).toBeGreaterThan(0);
  await bell.click();
  await expect(page).toHaveURL(/\/reports\?tab=alerts$/);
  const drop = page.locator("[data-testid=alert-item][data-rule=revenue_drop]");
  await expect(drop).toHaveCount(1);
  await expect(drop).toContainText("below the forecast range");
  await expect(drop).toContainText("Critical");
  await expect(drop.getByRole("link", { name: msg("en", "alerts.open.dashboard") })).toHaveAttribute(
    "href",
    `/dashboard?dataset=${datasetId}`,
  );

  // Mark one read, then the rest.
  await drop.getByRole("button", { name: /^Mark read:/ }).click();
  await expect(drop).toHaveAttribute("data-unread", "false");
  const markAll = page.getByRole("button", { name: msg("en", "alerts.markAllRead") });
  if ((await page.locator("[data-testid=alert-item][data-unread=true]").count()) > 0) await markAll.click();
  else await expect(markAll).toBeDisabled(); // nothing left to mark
  await expect(page.locator("[data-testid=alert-item][data-unread=true]")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("link", { name: "Alerts, none unread" })).toBeVisible();

  // Re-running the same analysis repeats nothing.
  const total = await page.getByTestId("alert-item").count();
  const rerun = await page.request.post(`/api/v1/datasets/${datasetId}/analyze`, { headers: bearer, data: {} });
  expect(rerun.status()).toBe(202);
  await expect
    .poll(
      async () => {
        const runs = await page.request.get(`/api/v1/datasets/${datasetId}/analysis-runs?limit=1`, { headers: bearer });
        return ((await runs.json()) as { status: string }[])[0]!.status;
      },
      { timeout: 600_000, intervals: [3_000] },
    )
    .toMatch(/^completed/);
  await page.reload();
  await expect(page.getByTestId("alert-item")).toHaveCount(total);

  // 375 px: no sideways scroll on either tab, and the header keeps one row.
  await page.setViewportSize({ width: 375, height: 812 });
  for (const url of ["/reports", "/reports?tab=alerts"]) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    expect((await page.locator("header").boundingBox())!.height).toBeLessThanOrEqual(57);
  }
});

/**
 * Screenshots for docs/screenshots/step-10 (opt-in: SCREENSHOT_DIR=<folder>). One company with
 * an alert and an English and an Arabic report, shown in EN/AR, light/dark, desktop and phone.
 */
test("step 10 screenshots", async ({ page, browser, baseURL }, testInfo) => {
  const dir = process.env.SCREENSHOT_DIR;
  test.skip(!dir, "set SCREENSHOT_DIR to take the Step 10 screenshots");
  testInfo.setTimeout(20 * 60_000);
  const files = versions();
  const user = await register(page, "en");
  const datasetId = await uploadAnalysed(page, files.v1);
  const bearer = { Authorization: `Bearer ${await token(page, user)}` };
  await page.request.post(`/api/v1/datasets/${datasetId}/versions`, {
    headers: bearer,
    multipart: { file: { name: "v2.csv", mimeType: "text/csv", buffer: readFileSync(files.v2) } },
  });
  await expect
    .poll(
      async () => {
        const runs = await page.request.get(`/api/v1/datasets/${datasetId}/analysis-runs?limit=1`, { headers: bearer });
        const [run] = (await runs.json()) as { version: number; status: string }[];
        return run && run.version === 2 ? run.status : "waiting";
      },
      { timeout: 600_000, intervals: [3_000] },
    )
    .toMatch(/^completed/);
  for (const locale of ["en", "ar"]) {
    const made = await page.request.post(`/api/v1/datasets/${datasetId}/reports`, { headers: bearer, data: { locale } });
    const id = (await made.json()).id as string;
    await expect
      .poll(async () => (await (await page.request.get(`/api/v1/reports/${id}`, { headers: bearer })).json()).status, {
        timeout: 120_000,
      })
      .toBe("completed");
  }
  await page.close();

  const shots = [
    { name: "reports", url: "/reports", ready: "[data-testid=report-list]" },
    { name: "alerts", url: "/reports?tab=alerts", ready: "[data-testid=alerts-feed]" },
    { name: "settings-alerts", url: "/settings", ready: "[data-testid=alert-settings-form]" },
  ];
  for (const locale of ["en", "ar"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const context = await browser.newContext({
          viewport: { width, height: width === 375 ? 812 : 900 },
          colorScheme: scheme,
          reducedMotion: "reduce",
        });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const shotPage = await context.newPage();
        await login(shotPage, user, locale);
        for (const shot of shots) {
          await shotPage.waitForTimeout(2_100);
          await shotPage.goto(shot.url);
          await shotPage.locator(shot.ready).first().waitFor({ timeout: 30_000 });
          await shotPage.waitForLoadState("networkidle");
          if (shot.name === "settings-alerts") {
            await shotPage.locator("#alert-settings-h").scrollIntoViewIfNeeded();
            await shotPage.screenshot({ path: path.join(dir!, `${shot.name}-${locale}-${scheme}-${width}.png`) });
          } else {
            await shotPage.screenshot({ path: path.join(dir!, `${shot.name}-${locale}-${scheme}-${width}.png`), fullPage: true });
          }
        }
        if (locale === "en" && scheme === "light" && width === 1440) {
          await shotPage.goto("/reports");
          await shotPage.getByTestId("report-generate").click();
          await shotPage.getByRole("dialog").waitFor();
          await shotPage.screenshot({ path: path.join(dir!, "generate-dialog-en-light-1440.png") });
        }
        await context.close();
      }
    }
  }
});
