import { expect, test, type Page } from "@playwright/test";

import { GENERATED_MESSY } from "./global-setup";
import {
  SAMPLE_MESSY,
  dialScore,
  expectNoAxeViolations,
  msg,
  register,
  setLocale,
  waitForAnalysis,
  type Locale,
} from "./helpers";

/** Upload a file from the upload page and land on its detail page. */
async function upload(page: Page, locale: Locale, file: string) {
  await page.goto("/datasets/new");
  await expect(page.getByRole("heading", { level: 1, name: msg(locale, "upload.title") })).toBeVisible();
  await page.getByTestId("file-input").setInputFiles(file);
  await page.getByRole("button", { name: msg(locale, "upload.submit") }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
}

/** Tick every recommended fix (pre-selected or not) plus "remove duplicates", then apply. */
async function applyRecommendedPlusDedupe(page: Page, locale: Locale): Promise<string[]> {
  const chosen: string[] = [];
  const rows = page.locator("li[data-fix]");
  for (const row of await rows.all()) {
    const id = (await row.getAttribute("data-fix")) ?? "";
    const recommended = (await row.getAttribute("data-recommended")) === "true";
    const box = row.getByRole("checkbox");
    if (recommended || id === "dedupe") {
      await box.check();
      chosen.push(id);
    } else {
      await expect(box, `${id} must start unticked`).not.toBeChecked();
    }
  }
  await page.getByRole("button", { name: msg(locale, "plan.apply") }).click();
  return chosen;
}

for (const locale of ["en", "ar"] as const) {
  test.describe(`journey (${locale})`, () => {
    test.beforeEach(async ({ context, baseURL }) => setLocale(context, locale, baseURL!));

    test(`register, upload, clean, change a role and run the analysis (${locale})`, async ({ page }, testInfo) => {
      testInfo.setTimeout(12 * 60_000);
      await page.goto("/register");
      await expect(page.locator("html")).toHaveAttribute("dir", locale === "ar" ? "rtl" : "ltr");
      await expectNoAxeViolations(page, `${locale}-register`);
      await register(page, locale);
      await expect(page.getByRole("heading", { name: msg(locale, "datasets.emptyTitle") })).toBeVisible();
      await expectNoAxeViolations(page, `${locale}-datasets-empty`);

      if (locale === "ar") {
        // Real RTL: the sidebar sits on the right of the content.
        const nav = await page.getByRole("navigation", { name: msg(locale, "nav.main") }).boundingBox();
        const main = await page.locator("main").boundingBox();
        expect(nav!.x).toBeGreaterThan(main!.x);
      }

      // 1. Sample messy file: low score, recommended fixes + dedupe, higher score.
      await upload(page, locale, SAMPLE_MESSY);
      await expectNoAxeViolations(page, `${locale}-upload-processing`);
      const before = await dialScore(page);
      testInfo.annotations.push({ type: "score-before", description: String(before) });
      expect(before).toBeLessThan(85);
      await expect(page.getByTestId("issues")).toBeVisible();
      await waitForAnalysis(page);
      await expectNoAxeViolations(page, `${locale}-detail-v1`);

      const chosen = await applyRecommendedPlusDedupe(page, locale);
      testInfo.annotations.push({ type: "fixes", description: chosen.join(", ") });
      await expect(page.getByTestId("version")).toHaveText(msg(locale, "common.version", { version: 2 }), {
        timeout: 60_000,
      });
      const after = await dialScore(page);
      testInfo.annotations.push({ type: "score-after", description: String(after) });
      expect(after).toBeGreaterThan(before);
      await expect(page.getByTestId("score-change")).toBeVisible();

      // 2. Wait for the automatic run on v2, change one role, accept the re-run prompt.
      await waitForAnalysis(page);
      const role = page.locator('[data-column="region"]').getByRole("combobox");
      await role.click();
      await page.getByRole("option", { name: msg(locale, "columns.roles.channel") }).click();
      const prompt = page.getByTestId("rerun-prompt");
      await expect(prompt).toBeVisible();
      await prompt.getByRole("button", { name: msg(locale, "columns.rerun") }).click();
      await expect(prompt).toBeHidden();

      // 3. Live per-module progress until the run finishes.
      const progress = page.getByTestId("analysis-progress");
      await expect(progress).toHaveAttribute("data-status", /^(pending|running)$/, { timeout: 15_000 });
      const seenRunning = page.locator('[data-module][data-state="running"]');
      await expect(seenRunning.first()).toBeVisible({ timeout: 60_000 });
      const status = await waitForAnalysis(page);
      testInfo.annotations.push({ type: "analysis", description: status });
      expect(["completed", "completed_with_warnings"]).toContain(status);
      const states = await page.locator("[data-module]").evaluateAll((els) =>
        els.map((el) => `${el.getAttribute("data-module")}=${el.getAttribute("data-state")}`),
      );
      testInfo.annotations.push({ type: "modules", description: states.join(" ") });
      expect(states.every((s) => /=(completed|skipped|failed)$/.test(s))).toBe(true);
      await expectNoAxeViolations(page, `${locale}-detail-analysed`);

      // 4. Generated messy file (duplicates, mixed dates, Arabic digits): same improvement.
      await upload(page, locale, GENERATED_MESSY);
      const genBefore = await dialScore(page);
      await applyRecommendedPlusDedupe(page, locale);
      await expect(page.getByTestId("version")).toHaveText(msg(locale, "common.version", { version: 2 }), {
        timeout: 60_000,
      });
      const genAfter = await dialScore(page);
      testInfo.annotations.push({ type: "generated", description: `${genBefore} -> ${genAfter}` });
      expect(genAfter).toBeGreaterThan(genBefore);

      // 5. Both datasets are listed with their status and score.
      await page.getByRole("link", { name: msg(locale, "nav.datasets") }).first().click();
      await expect(page.getByRole("link", { name: "retail_messy" }).first()).toBeVisible();
      await expect(page.getByRole("link", { name: "retail_gen_messy_en" }).first()).toBeVisible();
      await expectNoAxeViolations(page, `${locale}-datasets-list`);
    });
  });
}
