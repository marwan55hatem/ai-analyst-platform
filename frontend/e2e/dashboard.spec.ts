import { expect, test } from "@playwright/test";

import { GROUND_TRUTH } from "./global-setup";
import { analysedUser, login, msg } from "./helpers";

/**
 * Dashboard on two years of daily sales: answer first (KPIs vs the previous window), then the
 * trend with forecast and anomaly markers, contributors and what needs attention.
 */
test("dashboard shows KPIs, trend, contributors and attention, and renders fast", async ({ page, browser }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  await login(page, await analysedUser(browser, GROUND_TRUTH));

  // Time from the dashboard's own data arriving to the charts being drawn.
  let loadedAt = 0;
  page.on("response", (response) => {
    if (response.url().includes("/timeseries") && response.url().includes("start=")) loadedAt = Date.now();
  });
  await page.goto("/dashboard?range=all&grain=day");
  const dashboard = page.getByTestId("dashboard");
  await expect(dashboard).toHaveAttribute("data-ready", "true", { timeout: 30_000 });
  const renderMs = Date.now() - loadedAt;
  testInfo.annotations.push({ type: "render-ms", description: String(renderMs) });
  expect(loadedAt).toBeGreaterThan(0);
  expect(renderMs, "2 years of daily data render in under 2 s after the data loads").toBeLessThan(2000);
  const points = await page.evaluate(() => performance.getEntriesByName("dashboard-ready").length);
  expect(points).toBeGreaterThan(0);

  // KPIs: five cards with values; with all data there is nothing earlier to compare with.
  const cards = page.getByTestId("kpi-card");
  await expect(cards).toHaveCount(5);
  await expect(cards.first().getByTestId("kpi-value")).not.toHaveText("—");
  await expect(cards.first()).toContainText(msg("en", "dashboard.noComparison"));
  await expect(page.getByTestId("comparison-note")).toBeVisible();

  // Last 90 days: every KPI has a change vs the previous 90 days, as an arrow and words.
  await page.getByTestId("preset-90d").click();
  await expect(page).toHaveURL(/range=90d/);
  await expect(cards.first().locator("[data-direction]")).toHaveAttribute("data-direction", /^(up|down|flat)$/);
  await expect(page.getByTestId("range-summary")).toContainText("Dec 31, 2024");

  // Trend at the forecast's grain: the dashed line and both ranges are in the legend.
  await page.getByTestId("preset-all").click();
  await page.getByTestId("grain-month").click();
  await expect(page).toHaveURL(/range=all.*grain=month|grain=month.*range=all/);
  const trend = page.getByTestId("trend-chart");
  await expect(trend.getByText(msg("en", "charts.band80")).first()).toBeVisible();
  // A monthly line shows anomalies as ticks on the time axis, never as points on the total.
  await expect(trend.locator("[data-marker-tick=drop]").first()).toBeVisible();
  await expect(trend.locator("[data-marker]")).toHaveCount(0);
  await trend.getByRole("button", { name: msg("en", "charts.viewTable") }).click();
  await expect(trend.getByRole("table")).toContainText(msg("en", "charts.forecast"));

  // Contributors: one tab per dimension, bars with printed values.
  const contributors = page.getByTestId("contributors");
  await contributors.getByTestId("dim-category").click();
  await expect(contributors.getByTestId("bar-chart").locator("li")).toHaveCount(5);

  await expect(contributors.getByTestId("contributors-period")).toContainText("All time");

  // What needs attention: only actionable findings; one opens its evidence and the chat hand-off.
  const attention = page.getByTestId("attention").getByTestId("insight-list").locator("li");
  await expect(attention.first()).toBeVisible();
  expect(await attention.count()).toBeLessThanOrEqual(5);
  for (const name of await attention.locator("[data-module]").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-module")))) {
    expect(["anomalies", "pareto", "churn"]).toContain(name);
  }
  await attention.first().getByRole("button").click();
  const drawer = page.getByTestId("insight-drawer");
  await expect(drawer.getByTestId("evidence").first()).toContainText("SELECT");
  const ask = drawer.getByTestId("ask-analyst");
  await expect(ask).toHaveAttribute("href", /\/chat\?insight=[0-9a-f-]{36}$/);
  await ask.click();
  await expect(page.getByTestId("insight-chip")).toBeVisible();
});

test("insights feed filters on the server and keeps the drawer in the URL", async ({ page, browser }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/insights?module=forecast");
  const rows = page.getByTestId("insight-list").locator("li");
  await expect(rows.first()).toBeVisible();
  const modules = await rows.locator("button").evaluateAll((els) => els.map((e) => e.getAttribute("data-module")));
  for (const name of modules) {
    expect(name).toBe("forecast");
  }
  // Keyboard: Enter opens the drawer; Escape closes it and focus returns to the row.
  const row = rows.first().getByRole("button");
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("evidence").first()).toBeVisible();
  const started = Date.now();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("insight-drawer")).toHaveCount(0);
  testInfo.annotations.push({ type: "drawer-close-ms", description: String(Date.now() - started) });
  await expect(row).toBeFocused();
  await expect(page).not.toHaveURL(/insight=/);

  await rows.first().getByRole("button").click();
  await expect(page).toHaveURL(/insight=[0-9a-f-]{36}/);
  await page.reload();
  await expect(page.getByTestId("insight-drawer")).toBeVisible();
});

test("all data says there is no previous period", async ({ page, browser }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/dashboard?range=all");
  const summary = page.getByTestId("range-summary");
  await expect(summary).toContainText("no previous period");
  await expect(summary).not.toContainText("compared with");
});
