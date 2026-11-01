import { expect, test, type Page } from "@playwright/test";

import { GROUND_TRUTH } from "./global-setup";
import { SAMPLE_MESSY, askQuestion, dialScore, expectNoAxeViolations, msg, register, uploadAnalysed, waitForAnalysis } from "./helpers";

/** Names the elements that reach past the viewport, so a sideways scroll is easy to trace. */
async function widestElements(page: Page): Promise<string> {
  return page.evaluate(() => {
    const wide = Array.from(document.querySelectorAll("body *")).filter(
      (el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === "visible",
    );
    // Keep the innermost ones: the outer boxes only inherit the overflow.
    const inner = wide.filter((el) => !wide.some((other) => other !== el && el.contains(other)));
    return inner
      .slice(0, 6)
      .map((el) => `${el.tagName.toLowerCase()}(${el.scrollWidth}>${el.clientWidth})${el.className ? "." + String(el.className).slice(0, 50) : ""}`)
      .join(" | ");
  });
}

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
  // The dashboard and insights pages show the newest dataset: two years of daily sales.
  await uploadAnalysed(setup, GROUND_TRUTH);
  // A conversation showing what an answer can hold: chart, evidence, a verifier warning.
  await setup.goto("/chat");
  await askQuestion(setup, "Show a chart of revenue by month");
  await askQuestion(setup, "[verifier-demo] net revenue");
  const conversation = new URL(setup.url()).search;
  await setup.close();

  const publicPages = ["/login", "/register"];
  const appPages = [
    "/datasets",
    "/datasets/new",
    detail,
    "/dashboard",
    "/insights",
    "/insights/forecast",
    "/insights/segments",
    "/insights/churn",
    "/insights/what-if",
    "/chat",
    "/reports",
    "/settings",
  ];

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
          const wide = overflow > 0 ? await widestElements(page) : "";
          expect(overflow, `horizontal scroll on ${url} at ${width}px ${wide}`).toBeLessThanOrEqual(0);
        };
        for (const url of publicPages) await check(url);
        await page.goto("/login");
        await page.getByLabel(msg(locale, "auth.email")).fill(user.email);
        await page.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
        await page.getByRole("button", { name: msg(locale, "auth.loginSubmit") }).click();
        await expect(page).toHaveURL(/\/datasets$/);
        for (const url of appPages) await check(url);

        // The chat with answers, evidence open, and the conversation panel on a phone.
        await check(`/chat${conversation}`);
        for (const button of await page.getByRole("button", { name: msg(locale, "chat.how.title") }).all()) {
          await button.click();
        }
        await expect(page.getByTestId("tool-step").first()).toBeVisible();
        await expectNoAxeViolations(page, `${locale}-${scheme}-${width}_chat-evidence`);
        const scroll = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(scroll, `horizontal scroll on the chat at ${width}px`).toBeLessThanOrEqual(0);
        if (width === 375) {
          await page.getByRole("button", { name: msg(locale, "chat.openConversations") }).click();
          await expect(page.getByRole("dialog").getByTestId("conversation-list")).toBeVisible();
          await expectNoAxeViolations(page, `${locale}-${scheme}-${width}_chat-sheet`);
        }
        await context.close();
      }
    }
  }
});
