import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { GROUND_TRUTH } from "./global-setup";
import { SAMPLE_MESSY, dialScore, growToFullPage, msg, register, uploadAnalysed, waitForAnalysis } from "./helpers";

/**
 * Screenshots of every page: en/ar x light/dark x 375/1440. Run on demand:
 *   SCREENSHOTS=1 npx playwright test screenshots.spec.ts
 * then `npm run screenshots` to optimise them into docs/screenshots.
 */
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture screenshots");

// Raw captures go to the OS temp folder; scripts/screenshots.mjs optimises them into docs/.
const OUT = process.env.SCREENSHOT_DIR ?? path.join(os.tmpdir(), "analyst-screenshots-raw");

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(1200); // dial needle settles
}

test("capture every page", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(20 * 60_000);
  mkdirSync(OUT, { recursive: true });

  // Data: one cleaned and analysed dataset.
  const setup = await browser.newContext();
  const page = await setup.newPage();
  const user = await register(page, "en");
  await page.goto("/datasets/new");
  await page.getByTestId("file-input").setInputFiles(SAMPLE_MESSY);
  await page.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const detailUrl = new URL(page.url()).pathname;
  await dialScore(page);
  await page.getByRole("button", { name: msg("en", "plan.apply") }).click();
  await expect(page.getByTestId("version")).toHaveText(msg("en", "common.version", { version: 2 }), { timeout: 60_000 });
  await dialScore(page);
  await waitForAnalysis(page);
  // Two years of daily sales for the dashboard and insights pages (the newest dataset).
  await uploadAnalysed(page, GROUND_TRUTH);
  await setup.close();

  const pages: { name: string; url: string; auth: boolean; full?: boolean }[] = [
    { name: "login", url: "/login", auth: false },
    { name: "register", url: "/register", auth: false },
    { name: "datasets", url: "/datasets", auth: true },
    { name: "upload", url: "/datasets/new", auth: true },
    { name: "detail", url: detailUrl, auth: true, full: true },
    { name: "dashboard", url: "/dashboard", auth: true, full: true },
    { name: "insights", url: "/insights", auth: true, full: true },
    { name: "forecast", url: "/insights/forecast", auth: true, full: true },
    { name: "segments", url: "/insights/segments", auth: true, full: true },
    { name: "churn", url: "/insights/churn", auth: true, full: true },
    { name: "what-if", url: "/insights/what-if", auth: true },
    { name: "settings", url: "/settings", auth: true },
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
        const p = await context.newPage();
        for (const target of pages.filter((x) => !x.auth)) {
          await p.goto(target.url);
          await settle(p);
          await p.screenshot({ path: path.join(OUT, `${target.name}-${locale}-${scheme}-${width}.png`) });
        }
        await p.goto("/login");
        await p.getByLabel(msg(locale, "auth.email")).fill(user.email);
        await p.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
        await p.getByRole("button", { name: msg(locale, "auth.loginSubmit") }).click();
        await expect(p).toHaveURL(/\/datasets$/);
        for (const target of pages.filter((x) => x.auth)) {
          await p.goto(target.url);
          await settle(p);
          const restore = target.full ? await growToFullPage(p) : null;
          await p.screenshot({ path: path.join(OUT, `${target.name}-${locale}-${scheme}-${width}.png`) });
          await restore?.();
        }
        await context.close();
      }
    }
  }
});
