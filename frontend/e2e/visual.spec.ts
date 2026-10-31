import { expect, test, type Page } from "@playwright/test";

import { GROUND_TRUTH } from "./global-setup";
import { analysedUser, growToFullPage, login } from "./helpers";

/**
 * Visual snapshots of the dashboard and insights pages: en/ar x light/dark x 1440/375 px.
 * The data is generated with a fixed seed; the company name in the header and the
 * per-user ids are masked. Update with `npx playwright test visual.spec.ts --update-snapshots`.
 * Baselines are taken on the developer's machine (the e2e suite does not run in CI).
 */
const PAGES = [
  { name: "dashboard", url: "/dashboard", ready: "[data-testid=dashboard][data-ready=true]" },
  { name: "insights", url: "/insights", ready: "[data-testid=insight-list]" },
  { name: "forecast", url: "/insights/forecast", ready: "[data-testid=forecast-chart]" },
  { name: "segments", url: "/insights/segments", ready: "[data-testid=rfm-grid]" },
  { name: "churn", url: "/insights/churn", ready: "[data-testid=churn-drivers]" },
  { name: "what-if", url: "/insights/what-if", ready: "[data-testid=scenario]" },
];

async function settle(page: Page, selector: string) {
  await page.locator(selector).first().waitFor({ timeout: 30_000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

test("dashboard and insights pages match their snapshots", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(30 * 60_000);
  const user = await analysedUser(browser, GROUND_TRUTH);

  for (const locale of ["en", "ar"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const context = await browser.newContext({
          viewport: { width, height: width === 375 ? 812 : 900 },
          colorScheme: scheme,
          reducedMotion: "reduce",
        });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const page = await context.newPage();
        await login(page, user, locale);
        for (const target of PAGES) {
          // Every full page load refreshes the session, and refreshes are limited to 30 a minute.
          await page.waitForTimeout(2_100);
          await page.goto(target.url);
          await settle(page, target.ready);
          const restore = await growToFullPage(page);
          await expect(page).toHaveScreenshot(`${target.name}-${locale}-${scheme}-${width}.png`, {
            mask: [page.locator("header")],
            maxDiffPixelRatio: 0.01,
            animations: "disabled",
          });
          await restore();
        }
        await context.close();
      }
    }
  }
});
