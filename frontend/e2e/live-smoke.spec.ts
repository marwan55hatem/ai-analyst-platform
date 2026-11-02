import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { GROUND_TRUTH } from "./global-setup";
import { lastAnswer, register, sendQuestion, uploadAnalysed } from "./helpers";

/**
 * One real question through the real UI, and only by hand: it spends tokens. It needs LIVE_SMOKE=1
 * with the api on Groq and the worker on the scripted model (global-setup checks both).
 */
test.skip(!process.env.LIVE_SMOKE, "set LIVE_SMOKE=1 to ask one live question");

test("one short question to the real model", async ({ page }, testInfo) => {
  testInfo.setTimeout(8 * 60_000);
  await register(page, "en");
  await uploadAnalysed(page, GROUND_TRUTH);
  await page.goto("/chat");
  await expect(page.getByTestId("chat-empty")).toBeVisible();

  const started = Date.now();
  await page.evaluate(() => performance.mark("chat-sent"));
  await sendQuestion(page, "What is the total net revenue?");
  const answer = lastAnswer(page);
  await expect(answer).toHaveAttribute("data-status", /^(done|failed|unanswered)$/, { timeout: 180_000 });
  const seconds = (Date.now() - started) / 1000;
  const firstToken = await page.evaluate(() => {
    const [first] = performance.getEntriesByName("chat-first-token");
    const [sent] = performance.getEntriesByName("chat-sent");
    return first && sent ? Math.round(first.startTime - sent.startTime) : null;
  });

  // Absent parts are normal here, so nothing may wait for them: read what is on the page now.
  const report = await answer.evaluate((element) => {
    const part = (id: string) => element.querySelector(`[data-testid=${id}]`)?.textContent ?? null;
    return {
      status: element.getAttribute("data-status"),
      error: element.querySelector("[data-testid=chat-error]")?.getAttribute("data-code") ?? null,
      testModelBadge: part("test-model"),
      verifierOk: part("verifier-ok"),
      verifierWarning: part("verifier-warning"),
      answer: part("answer-text"),
    };
  });
  await page.screenshot({ path: path.join(testInfo.outputDir, "live-answer.png") });
  await page.goto("/settings");
  await expect(page.getByTestId("ai-usage")).toBeVisible();
  const usage = await page.getByTestId("ai-usage").locator("p").first().textContent();

  const dir = path.join(import.meta.dirname, "..", "test-results");
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "live-smoke.json"), JSON.stringify({ ...report, seconds, firstTokenMs: firstToken, usage }, null, 2));
});
