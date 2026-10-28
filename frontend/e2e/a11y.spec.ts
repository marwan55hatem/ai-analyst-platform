import { expect, test } from "@playwright/test";

import { SAMPLE_MESSY, dialScore, expectNoAxeViolations, msg, register, waitForAnalysis } from "./helpers";

/** axe (WCAG 2.2 A/AA) on every page: en/ar x light/dark x 1440/375, plus no sideways scroll. */
test("every page passes axe in both languages, themes and widths", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(15 * 60_000);
  const setup = await browser.newPage();
  const user = await register(setup, "en");
  await setup.goto("/datasets/new");
  await setup.getByTestId("file-input").setInputFiles(SAMPLE_MESSY);
  await setup.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(setup).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const detail = new URL(setup.url()).pathname;
  await dialScore(setup);
  await waitForAnalysis(setup);
  await setup.close();

  const publicPages = ["/login", "/register"];
  const appPages = ["/datasets", "/datasets/new", detail, "/dashboard", "/insights", "/chat", "/reports", "/settings"];

  for (const locale of ["en", "ar"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const page = await context.newPage();
        const check = async (url: string) => {
          await page.goto(url);
          await page.waitForLoadState("networkidle");
          const name = url === detail ? "detail" : url.replace(/\//g, "_");
          await expectNoAxeViolations(page, `${locale}-${scheme}-${width}${name}`);
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
          expect(overflow, `horizontal scroll on ${url} at ${width}px`).toBeLessThanOrEqual(0);
        };
        for (const url of publicPages) await check(url);
        await page.goto("/login");
        await page.getByLabel(msg(locale, "auth.email")).fill(user.email);
        await page.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
        await page.getByRole("button", { name: msg(locale, "auth.loginSubmit") }).click();
        await expect(page).toHaveURL(/\/datasets$/);
        for (const url of appPages) await check(url);
        await context.close();
      }
    }
  }
});
