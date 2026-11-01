import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { GROUND_TRUTH } from "./global-setup";
import { analysedUser, askQuestion, login, msg, type Locale } from "./helpers";

/**
 * The Step 13 walkthrough: en/ar x light/dark x 375/1440, with a screenshot and a pass or fail for
 * each check. Run on demand:
 *   WALKTHROUGH=1 SCREENSHOT_DIR=<temp> npx playwright test walkthrough.spec.ts
 *   SCREENSHOT_DIR=<temp> SCREENSHOT_OUT=../docs/screenshots/step-13 npm run screenshots
 */
test.skip(!process.env.WALKTHROUGH, "set WALKTHROUGH=1 to run the walkthrough");

const OUT = process.env.SCREENSHOT_DIR ?? path.join(os.tmpdir(), "analyst-step13-raw");

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);
}

test("chat and settings in every language, theme and width", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(30 * 60_000);
  mkdirSync(OUT, { recursive: true });
  const user = await analysedUser(browser, GROUND_TRUTH);
  const results: { view: string; check: string; ok: boolean }[] = [];

  for (const locale of ["en", "ar"] as Locale[]) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const view = `${locale}-${scheme}-${width}`;
        const context = await browser.newContext({
          viewport: { width, height: width === 375 ? 812 : 900 },
          colorScheme: scheme,
          reducedMotion: "reduce",
        });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const page = await context.newPage();
        const check = async (name: string, passed: () => Promise<boolean>) => {
          const ok = await passed().catch(() => false);
          results.push({ view, check: name, ok });
          expect.soft(ok, `${view}: ${name}`).toBe(true);
        };
        const fits = async () =>
          (await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)) <= 0;
        const shot = (name: string) => page.screenshot({ path: path.join(OUT, `${name}-${view}.png`) });

        await login(page, user, locale);
        await page.goto("/chat");
        await settle(page);
        await check("empty conversation shows starters", () => page.getByTestId("starters").isVisible());
        await check("no sideways scroll, empty", fits);
        await shot("chat-empty");

        const question = locale === "ar" ? "ما هو صافي الإيراد؟" : "What is our total net revenue?";
        const answer = await askQuestion(page, question, locale);
        await check("answer finished", async () => (await answer.getAttribute("data-status")) === "done");
        await check("verifier badge shown", () => answer.getByTestId("verifier-ok").isVisible());
        await check("confidence shown", () => answer.locator("[data-confidence]").isVisible());
        await answer.getByRole("button", { name: msg(locale, "chat.how.title") }).click();
        await check("evidence opens", () => answer.getByTestId("tool-step").first().isVisible());
        await check("no sideways scroll, answer", fits);
        await settle(page);
        await shot("chat-answer");

        const chart = await askQuestion(page, "Show a chart of revenue by month", locale);
        await check("chart shown", () => chart.getByTestId("chat-chart").isVisible());
        await chart.scrollIntoViewIfNeeded();
        await settle(page);
        await shot("chat-chart");

        await page.goto("/settings");
        await settle(page);
        await check("usage shown", () => page.getByTestId("ai-usage").isVisible());
        await check("privacy shown", () => page.getByTestId("privacy").isVisible());
        await check("no sideways scroll, settings", fits);
        await shot("settings");
        await context.close();
      }
    }
  }
  const dir = path.join(import.meta.dirname, "..", "test-results");
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "walkthrough.json"), JSON.stringify(results, null, 2));
});
