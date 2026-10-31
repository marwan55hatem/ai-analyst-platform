import { expect, test } from "@playwright/test";

import { msg, register } from "./helpers";

/**
 * The frontend proxies /api/v1 to the API, and Next cuts proxied request bodies at 10 MB unless
 * told otherwise. A 12 MB file must still arrive whole (the app promises files up to 50 MB).
 */
test("a file larger than 10 MB uploads through the proxy", async ({ page }) => {
  await register(page, "en");
  const rows = ["order_id,order_date,product,region,quantity,unit_price,revenue"];
  for (let i = 0; i < 300_000; i++) {
    rows.push(`${i},2024-0${1 + (i % 9)}-1${i % 9},P${i % 500},R${i % 6},${1 + (i % 5)},${10 + (i % 90)}.50,${(1 + (i % 5)) * (10 + (i % 90))}.50`);
  }
  const buffer = Buffer.from(rows.join("\n"));
  expect(buffer.length).toBeGreaterThan(10 * 1024 * 1024);

  await page.goto("/datasets/new");
  await page.getByTestId("file-input").setInputFiles({ name: "large.csv", mimeType: "text/csv", buffer });
  await page.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
});
