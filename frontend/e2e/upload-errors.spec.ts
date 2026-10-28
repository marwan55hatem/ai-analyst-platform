import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { FIXTURE_DIR } from "./global-setup";

import { expectNoAxeViolations, msg, register } from "./helpers";

const MB = 1024 * 1024;

test("failed uploads explain what went wrong", async ({ page }) => {
  await register(page, "en");
  await page.goto("/datasets/new");
  await expectNoAxeViolations(page, "en-upload");
  const input = page.getByTestId("file-input");
  const alert = page.locator("main [role=alert]");

  // Wrong type: refused in the browser before upload.
  await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(alert).toContainText('"notes.txt" isn\'t a CSV, TSV or .xlsx file');

  // Empty file.
  await input.setInputFiles({ name: "empty.csv", mimeType: "text/csv", buffer: Buffer.alloc(0) });
  await expect(alert).toContainText('"empty.csv" is empty');

  // Over the size limit (50 MB in compose), with the actual size.
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const big = path.join(FIXTURE_DIR, "big.csv");
  writeFileSync(big, Buffer.alloc(51 * MB, 0x61));
  await input.setInputFiles(big);
  await expect(alert).toContainText(/"big.csv" is 51 MB, over the 50 MB limit/);

  // Binary content behind a .csv name: only the server can tell; its code is translated.
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d, 0x49, 0x48, 0x44, 0x52]);
  await input.setInputFiles({ name: "photo.csv", mimeType: "text/csv", buffer: png });
  await expect(alert).toBeHidden();
  await page.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(alert).toHaveText(msg("en", "errors.content_mismatch"));
  await expectNoAxeViolations(page, "en-upload-error");
});
