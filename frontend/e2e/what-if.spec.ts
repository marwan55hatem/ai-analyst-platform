import { expect, test } from "@playwright/test";

import { GROUND_TRUTH } from "./global-setup";
import { analysedUser, login, msg } from "./helpers";

/** What-if: three scenarios, changes kept within +/-30%, ranges, assumptions and warnings. */
test("what-if compares three scenarios as ranges with assumptions and warnings", async ({ page, browser }, testInfo) => {
  testInfo.setTimeout(10 * 60_000);
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/insights/what-if");

  await page.getByTestId("add-scenario").click();
  await page.getByTestId("add-scenario").click();
  await expect(page.getByTestId("scenario")).toHaveCount(3);
  await expect(page.getByTestId("add-scenario")).toHaveCount(0);

  // Typed values outside +/-30% are clamped before anything is sent.
  await page.getByTestId("change-0").fill("45");
  await expect(page.getByTestId("change-0")).toHaveValue("30");
  await page.getByTestId("change-2").fill("-50");
  await expect(page.getByTestId("change-2")).toHaveValue("-30");

  // A category scope needs a target before the estimate can run.
  await page.getByRole("combobox", { name: msg("en", "whatIf.scope"), exact: true }).nth(1).click();
  await page.getByRole("option", { name: msg("en", "whatIf.scopes.category") }).click();
  await expect(page.getByTestId("run-whatif")).toBeDisabled();
  await page.getByRole("combobox", { name: msg("en", "whatIf.target.category"), exact: true }).click();
  await page.getByRole("option").first().click();
  await expect(page.getByTestId("run-whatif")).toBeEnabled();

  await page.getByTestId("run-whatif").click();
  const results = page.getByTestId("scenario-result");
  await expect(results).toHaveCount(3, { timeout: 60_000 });
  // Each answer names the change that was sent: clamped to +/-30%, the category scenario
  // answered for the chosen category.
  await expect(results.nth(0).getByRole("heading", { level: 3 })).toContainText("+30%");
  await expect(results.nth(2).getByRole("heading", { level: 3 })).toContainText("-30%");
  await expect(results.nth(1)).not.toContainText(msg("en", "whatIf.scopes.all"));
  // +30% price goes beyond the prices in the data: the warning comes first, as an alert.
  const first = results.first();
  await expect(first.getByTestId("whatif-warning")).toContainText(msg("en", "whatIf.outOfRange"));
  await expect(first.getByRole("alert")).toBeVisible();
  for (const result of await results.all()) {
    const status = await result.getAttribute("data-status");
    if (status === "ok") await expect(result.getByTestId("range-bar")).toHaveCount(2);
    else await expect(result.getByTestId("not-enough-evidence")).toBeVisible();
    await expect(result.getByTestId("assumptions").locator("li").first()).toBeVisible();
  }
});
