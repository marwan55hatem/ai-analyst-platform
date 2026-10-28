import { expect, test } from "@playwright/test";

import { expectNoAxeViolations, msg, register } from "./helpers";

test.describe("session", () => {
  test("reload keeps the session, logout ends it, protected pages redirect", async ({ page }) => {
    await page.goto("/login");
    await expectNoAxeViolations(page, "en-login");
    const user = await register(page, "en");

    // The access token lives in memory only: a reload must restore it from the cookie.
    const refreshes: number[] = [];
    page.on("response", (r) => {
      if (r.url().endsWith("/api/v1/auth/refresh")) refreshes.push(r.status());
    });
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: msg("en", "datasets.title") })).toBeVisible();
    await expect(page.getByText(user.company)).toBeVisible();
    expect(refreshes).toEqual([200]);
    const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
    expect(stored).not.toMatch(/eyJ/); // no JWT in web storage

    // Logout, then the protected page sends us to login with a return path.
    await page.getByRole("button", { name: msg("en", "header.account") }).click();
    await page.getByRole("menuitem", { name: msg("en", "header.signOut") }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/datasets/new");
    await expect(page).toHaveURL(/\/login\?next=%2Fdatasets%2Fnew/);

    // Logging in again returns to the page we asked for.
    await page.getByLabel(msg("en", "auth.email")).fill(user.email);
    await page.getByLabel(msg("en", "auth.password"), { exact: true }).fill(user.password);
    await page.getByRole("button", { name: msg("en", "auth.loginSubmit") }).click();
    await expect(page).toHaveURL(/\/datasets\/new$/);
  });

  test("a wrong password shows a clear message", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(msg("en", "auth.email")).fill("nobody@example.com");
    await page.getByLabel(msg("en", "auth.password"), { exact: true }).fill("not-the-password-1");
    await page.getByRole("button", { name: msg("en", "auth.loginSubmit") }).click();
    await expect(page.locator("main [role=alert]")).toHaveText(msg("en", "auth.badCredentials"));
  });
});
