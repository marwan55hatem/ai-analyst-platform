import { expect, test } from "@playwright/test";

import { SAMPLE_MESSY, dialScore, msg, register } from "./helpers";

/** Everything in the first journey works with the keyboard alone. */
test("keyboard only: skip link, menus, upload, role dropdown, cleaning plan", async ({ page }) => {
  const user = await register(page, "en");

  // Skip link is the first stop and moves focus to the main content.
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: msg("en", "nav.skip") });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("main#main")).toBeFocused();

  // Sidebar links are reachable and announce the current page.
  const datasetsLink = page.getByRole("navigation", { name: msg("en", "nav.main") }).getByRole("link", { name: msg("en", "nav.datasets") });
  await expect(datasetsLink).toHaveAttribute("aria-current", "page");

  // Theme menu opens with Enter, moves with arrows, closes with Escape.
  const theme = page.getByRole("button", { name: msg("en", "header.theme") });
  await theme.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitemradio", { name: msg("en", "header.themeDark") })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(theme).toBeFocused();

  // Upload with the keyboard: the visible button opens the picker.
  await page.goto("/datasets/new");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: msg("en", "upload.choose") }).focus();
  await page.keyboard.press("Enter");
  await (await chooser).setFiles(SAMPLE_MESSY);
  await page.getByRole("button", { name: msg("en", "upload.submit") }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await dialScore(page);

  // Cleaning plan: Space toggles a checkbox, arrows move between strategies.
  const dedupeOrFirst = page.locator("li[data-fix]").last().getByRole("checkbox");
  await dedupeOrFirst.focus();
  const before = await dedupeOrFirst.isChecked();
  await page.keyboard.press("Space");
  await expect(dedupeOrFirst).toBeChecked({ checked: !before });
  const nulls = page.locator('li[data-fix^="nulls:"]').first();
  if (await nulls.count()) {
    const keep = nulls.getByRole("radio", { name: msg("en", "plan.strategies.keep") });
    await nulls.getByRole("checkbox").focus();
    await page.keyboard.press("Tab");
    await expect(keep).toBeFocused();
    // A real key press lasts longer than Radix's deferred focus move; press() would not.
    await page.keyboard.down("ArrowDown");
    await page.waitForTimeout(80);
    await page.keyboard.up("ArrowDown");
    await expect(keep).not.toBeChecked();
  }

  // Role dropdown: Enter opens, arrows move, Enter selects; focus returns to the trigger.
  const role = page.locator('[data-column="region"]').getByRole("combobox");
  await role.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("listbox")).toBeVisible();
  // Wait for each step like a person would: the current role is focused, then the next.
  await expect(page.getByRole("option", { name: msg("en", "columns.roles.region") })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("option", { name: msg("en", "columns.roles.channel") })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("rerun-prompt")).toBeVisible();
  await expect(page.getByRole("listbox")).toBeHidden();
  await expect(role).toBeFocused();

  // Account menu: sign out with the keyboard.
  await page.getByRole("button", { name: msg("en", "header.account") }).focus();
  await page.keyboard.press("Enter");
  const signOut = page.getByRole("menuitem", { name: msg("en", "header.signOut") });
  await expect(signOut).toBeVisible();
  await page.keyboard.press("End"); // last item, whichever item opening focused
  await expect(signOut).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/login/);
  expect(user.email).toContain("@");
});
