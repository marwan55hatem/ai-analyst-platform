import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { growToFullPage, msg, register, waitForAnalysis, type Locale } from "./helpers";

/**
 * Items 1-11 of the Step 12b brief in a real browser, on the 309,254-row file. The file is kept
 * out of the repository, so this spec is opt-in: LARGE_SAMPLE=1 npx playwright test large-sample.
 */
const SAMPLES = path.resolve(import.meta.dirname, "..", "..", "evaluation", "datasets", "samples");
const FILE = path.join(SAMPLES, "sales_large_clean.csv");
const OUT = path.resolve(import.meta.dirname, "..", "..", "docs", "screenshots", "step-12b");
const facts = existsSync(path.join(SAMPLES, "sales_large_clean_facts.json"))
  ? (JSON.parse(readFileSync(path.join(SAMPLES, "sales_large_clean_facts.json"), "utf8")) as { planted_anomaly_days: { date: string }[] })
  : { planted_anomaly_days: [] };

test.skip(!process.env.LARGE_SAMPLE || !existsSync(FILE), "needs LARGE_SAMPLE=1 and the sample file");

async function shot(page: Page, name: string) {
  const restore = await growToFullPage(page);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  await restore();
}

/** The plot must end inside its card: an overflowing chart slides under the next panel. */
async function plotFits(page: Page): Promise<boolean> {
  return page.locator("[role=img] .recharts-xAxis").first().evaluate((axis) => {
    const box = axis.closest("[role=img]")!.getBoundingClientRect();
    return axis.getBoundingClientRect().right <= box.right;
  });
}

async function signIn(page: Page, email: string, password: string, locale: Locale) {
  await page.goto("/login");
  await page.getByLabel(msg(locale, "auth.email")).fill(email);
  await page.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(password);
  await page.getByRole("button", { name: msg(locale, "auth.loginSubmit") }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 30_000 });
}

test("every Step 12b fix holds on the large file in both languages and themes", async ({ browser, baseURL }, testInfo) => {
  testInfo.setTimeout(20 * 60_000);
  mkdirSync(OUT, { recursive: true });
  const setup = await browser.newPage();
  const user = await register(setup, "en");
  await setup.goto("/datasets/new");
  await setup.getByTestId("file-input").setInputFiles(FILE);
  await setup.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(setup).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 120_000 });
  const detail = new URL(setup.url()).pathname;
  await waitForAnalysis(setup);
  await setup.close();

  const results: string[] = [];
  const check = async (name: string, passed: boolean) => {
    results.push(`${passed ? "PASS" : "FAIL"}  ${name}`);
    expect.soft(passed, name).toBe(true);
  };

  for (const locale of ["en", "ar"] as const) {
    for (const scheme of ["light", "dark"] as const) {
      for (const width of [1440, 375]) {
        const label = `${locale}-${scheme}-${width}`;
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
        await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL! }]);
        const page = await context.newPage();
        page.setDefaultTimeout(20_000);
        await page.waitForTimeout(13_000); // sign-ins are rate limited per address
        await signIn(page, user.email, user.password, locale);

        await page.goto(detail);
        const intro = page.getByTestId("plan-intro");
        await intro.waitFor({ timeout: 30_000 }).catch(() => undefined);
        if (await intro.count()) {
          const ticked = await page.getByRole("checkbox", { checked: true }).count();
          const text = (await intro.textContent()) ?? "";
          await check(`${label} 1 intro matches the selection`, ticked > 0 ? text === msg(locale, "plan.intro") : text === msg(locale, "plan.introNoneRecommended"));
        } else {
          await check(`${label} 1 no fix needed, so no intro`, true);
        }
        const body = (await page.locator("main").textContent()) ?? "";
        await check(`${label} 2 no negative outlier range`, !/\(-\d/.test(body));
        await shot(page, `data-health-${label}`);

        // The default range has an earlier period, so every card shows its comparison.
        await page.goto("/dashboard");
        await page.locator("[data-testid=dashboard][data-ready=true]").waitFor({ timeout: 30_000 });
        await page.waitForLoadState("networkidle");
        const margin = page.locator("[data-kpi=margin_pct]");
        const revenue = page.locator("[data-kpi=net_revenue]");
        const changeBox = await margin.locator("[data-direction]").boundingBox();
        const compareBox = await margin.getByTestId("kpi-comparison").boundingBox();
        await check(`${label} 6 margin change and label stack like the others`, Boolean(changeBox && compareBox && compareBox.y >= changeBox.y + changeBox.height - 1 && changeBox.height < 30));
        await check(`${label} 6 cards share one height`, width !== 1440 || (await margin.boundingBox())?.height === (await revenue.boundingBox())?.height);
        await check(`${label} 7 sparkline has about 13 weekly points`, Math.abs((await page.getByTestId("sparkline").first().locator("path").getAttribute("d"))!.split("L").length - 12) <= 2);
        await shot(page, `dashboard-90d-${label}`);

        await page.waitForTimeout(2_500);
        await page.goto("/dashboard?range=all&grain=month");
        await page.locator("[data-testid=dashboard][data-ready=true]").waitFor({ timeout: 30_000 });
        await page.waitForLoadState("networkidle");
        const dashboard = page.getByTestId("dashboard");
        const attention = page.getByTestId("attention").locator("[data-insight]");
        const modules = await attention.evaluateAll((rows) => rows.map((r) => r.getAttribute("data-module")));
        await check(`${label} 4 attention is actionable, at most 5 (${modules.length})`, modules.length <= 5 && modules.length > 0 && modules.every((m) => ["anomalies", "pareto", "churn"].includes(m ?? "")));
        await check(`${label} 5 contributors say all time`, (await page.getByTestId("contributors-period").textContent())?.includes(msg(locale, "dashboard.contributors.allTime").split(" (")[0]!) ?? false);
        await check(`${label} 7 sparkline has about 195 weekly points`, Math.abs((await page.getByTestId("sparkline").first().locator("path").getAttribute("d"))!.split("L").length - 195) < 5);
        await check(`${label} 9 month grain uses axis ticks`, (await page.locator("[data-marker-tick]").count()) >= 5 && (await page.locator("[data-marker]").count()) === 0);
        await check(`${label} 8 five unusual days listed`, (await page.getByTestId("event-list").getByRole("listitem").count()) === facts.planted_anomaly_days.length);
        const swatch = page.getByTestId("swatch-80").first();
        await check(`${label} 10 both band swatches shown`, (await swatch.isVisible()) && (await page.getByTestId("swatch-95").first().isVisible()));
        await check(`${label} no sideways scroll`, (await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)) <= 0);
        await shot(page, `dashboard-month-${label}`);
        await check(`${label} 10 chart stays inside its card after the screenshot`, await plotFits(page));

        await page.goto("/dashboard?range=all&grain=day");
        await page.locator("[data-testid=dashboard][data-ready=true]").waitFor({ timeout: 30_000 });
        await page.waitForLoadState("networkidle");
        await check(`${label} 9 day grain keeps point markers`, (await page.locator("[data-marker]").count()) >= 5 && (await page.locator("[data-marker-tick]").count()) === 0);
        await dashboard.waitFor();

        await page.waitForTimeout(2_500);
        await page.goto("/insights/forecast");
        await page.getByTestId("backtest").waitFor({ timeout: 30_000 });
        const forecast = (await page.locator("main").textContent()) ?? "";
        await check(`${label} 11 forecast is LightGBM, not last year`, forecast.includes("LightGBM") && forecast.replace(/\s/g, "").includes("3,134,153") === (locale === "en" || forecast.includes("3,134,153")));
        await shot(page, `forecast-${label}`);
        await check(`${label} 10 forecast chart stays inside its card`, await plotFits(page));

        if (width === 375) {
          // The name may wrap to the two lines the header clamps to; a third would be cut off.
          // Lines are counted because Arabic line boxes are taller than the paragraph's box.
          const lines = await page.locator("header p span[dir=auto]").evaluate((el) => new Set([...el.getClientRects()].map((r) => Math.round(r.top))).size);
          await check(`${label} 17 company name fits the header (${lines} line)`, lines <= 2);
        }
        await context.close();
      }
    }
  }
  console.log(results.join("\n"));
});
