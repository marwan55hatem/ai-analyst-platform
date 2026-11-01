import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import { GROUND_TRUTH } from "./global-setup";
import {
  SAMPLE_MESSY,
  analysedUser,
  askQuestion,
  dialScore,
  lastAnswer,
  login,
  msg,
  register,
  sendQuestion,
  setLocale,
  spendDailyBudget,
} from "./helpers";

/** Chat against the scripted model; global-setup stops the run on any other provider. */
async function startChat(page: Page, browser: Parameters<typeof analysedUser>[0]): Promise<void> {
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/chat");
  await expect(page.getByTestId("chat-empty")).toBeVisible();
}

const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

test.describe.configure({ timeout: 10 * 60_000 });

test("streams an answer with its tools, SQL and verifier badge, and keeps them after a reload", async ({ page, browser }) => {
  await startChat(page, browser);
  await expect(page.getByTestId("starters").getByRole("button").first()).toBeVisible();

  const answer = await askQuestion(page, "What are our top products?");
  await expect(answer.getByTestId("answer-text")).toContainText("Recommendation");
  await expect(answer.getByTestId("verifier-ok")).toBeVisible();

  await answer.getByRole("button", { name: msg("en", "chat.how.title") }).click();
  await expect(answer.getByTestId("tool-step")).toHaveCount(2);
  const sql = answer.locator("pre").first();
  await expect(sql).toContainText("SELECT");
  await expect(sql).toHaveAttribute("dir", "ltr");
  await expect(answer.getByText(/Showing \d+ of \d+ rows/)).toBeVisible();

  await page.reload();
  const stored = lastAnswer(page);
  await expect(stored).toHaveAttribute("data-status", "done");
  await expect(stored.getByTestId("verifier-ok")).toBeVisible();
  await expect(page.getByTestId("conversation-list").getByText("What are our top products?")).toBeVisible();
});

test("measures the time to the first token", async ({ page, browser }) => {
  await startChat(page, browser);
  const times: number[] = [];
  for (let run = 0; run < 3; run += 1) {
    await page.evaluate(() => {
      performance.clearMarks("chat-first-token");
      performance.mark("chat-sent");
    });
    await askQuestion(page, "What is our total net revenue?");
    times.push(
      await page.evaluate(() => {
        const [first] = performance.getEntriesByName("chat-first-token");
        const [sent] = performance.getEntriesByName("chat-sent");
        performance.clearMarks("chat-sent");
        return Math.round((first?.startTime ?? 0) - (sent?.startTime ?? 0));
      }),
    );
  }
  const dir = path.join(import.meta.dirname, "..", "test-results");
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, "ttft.json"), JSON.stringify({ model: "scripted", milliseconds: times }));
  for (const time of times) expect(time).toBeGreaterThan(0);
});

test("Stop keeps the partial answer, marked and unchecked, and the next question comes after it", async ({ page, browser }) => {
  await startChat(page, browser);
  await sendQuestion(page, "[slow-demo] what is the net revenue?");
  await expect(lastAnswer(page).getByTestId("answer-text")).toContainText("Scripted", { timeout: 30_000 });
  await page.getByRole("button", { name: msg("en", "chat.composer.stop") }).click();

  const stopped = page.getByTestId("answer").first();
  await expect(stopped).toHaveAttribute("data-status", "stopped");
  await expect(stopped.getByTestId("answer-stopped")).toBeVisible();
  await expect(stopped.getByTestId("verifier-unchecked")).toBeVisible();
  await expect(stopped.getByTestId("verifier-ok")).toHaveCount(0);
  const words = (await stopped.getByTestId("answer-text").textContent()) ?? "";
  await page.waitForTimeout(500);
  expect(await stopped.getByTestId("answer-text").textContent()).toBe(words);

  // Straight on to the next question: the server may still be closing the stopped turn.
  await askQuestion(page, "What is our total net revenue?");

  await page.reload();
  await expect(page.getByTestId("answer")).toHaveCount(2);
  await expect(page.getByTestId("answer").nth(0)).toHaveAttribute("data-status", "stopped");
  await expect(page.getByTestId("answer").nth(1)).toHaveAttribute("data-status", "done");
  await expect(page.getByTestId("answer").nth(0).getByTestId("verifier-unchecked")).toBeVisible();
});

test("a rate-limit wait shows a calm notice that goes when the answer resumes", async ({ page, browser }) => {
  await startChat(page, browser);
  await sendQuestion(page, "[rate-limit-demo] what is the net revenue?");
  const notice = page.getByTestId("status-notice");
  await expect(notice).toContainText("The model is busy, retrying in");
  await expect(notice).toHaveCount(0, { timeout: 15_000 });
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done", { timeout: 30_000 });
  await expect(lastAnswer(page).getByTestId("test-model")).toBeVisible();
});

test("a spent daily budget says so, sends you to usage and offers no retry", async ({ page }) => {
  const user = await register(page, "en");
  await page.goto("/datasets/new");
  await page.getByTestId("file-input").setInputFiles(SAMPLE_MESSY);
  await page.getByRole("button", { name: msg("en", "upload.submit") }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await dialScore(page);
  spendDailyBudget(user.email);

  await page.goto("/chat");
  await sendQuestion(page, "What is our total net revenue?");
  const error = page.getByTestId("chat-error");
  await expect(error).toHaveAttribute("data-code", "budget_exceeded");
  await expect(error).toContainText("today's AI budget");
  await expect(error.getByRole("button", { name: msg("en", "chat.retry") })).toHaveCount(0);

  await error.getByRole("link", { name: msg("en", "chat.errors.seeUsage") }).click();
  await expect(page.getByTestId("ai-usage")).toContainText(msg("en", "settings.ai.reached"));
});

test("a finding opens the chat with its question and a chip that can be removed", async ({ page, browser }) => {
  await login(page, await analysedUser(browser, GROUND_TRUTH));
  await page.goto("/insights");
  await page.getByTestId("insight-list").locator("li").first().getByRole("button").click();
  await page.getByTestId("ask-analyst").click();

  await expect(page.getByTestId("insight-chip")).toBeVisible();
  const box = page.getByRole("textbox", { name: msg("en", "chat.composer.label") });
  await expect(box).toHaveValue(/Tell me more about this finding/);

  await page.getByRole("button", { name: msg("en", "chat.insight.remove") }).click();
  await expect(page.getByTestId("insight-chip")).toHaveCount(0);
  await expect(box).toHaveValue("");
  await expect(page).not.toHaveURL(/insight=/);
});

test("an Arabic question reads right to left while SQL stays left to right", async ({ page, browser, baseURL, context }) => {
  await setLocale(context, "ar", baseURL!);
  await login(page, await analysedUser(browser, GROUND_TRUTH), "ar");
  await page.goto("/chat");

  const answer = await askQuestion(page, "ما هو صافي الإيراد؟", "ar");
  const text = answer.getByTestId("answer-text");
  await expect(text).toContainText("التوصية");
  const direction = await text.locator("p").nth(1).evaluate((el) => getComputedStyle(el).direction);
  expect(direction).toBe("rtl");
  const asked = page.getByTestId("user-message").locator("p");
  expect(await asked.evaluate((el) => getComputedStyle(el).direction)).toBe("rtl");

  const sql = await askQuestion(page, "top products", "ar");
  await sql.getByRole("button", { name: msg("ar", "chat.how.title") }).click();
  const code = sql.locator("pre").first();
  await expect(code).toContainText("SELECT");
  expect(await code.evaluate((el) => getComputedStyle(el).direction)).toBe("ltr");
});

test("the whole flow works from the keyboard", async ({ page, browser }) => {
  await startChat(page, browser);
  const box = page.getByRole("textbox", { name: msg("en", "chat.composer.label") });
  await box.focus();
  await page.keyboard.type("[slow-demo] what is the net revenue?");
  await page.keyboard.down("Shift");
  await page.keyboard.press("Enter");
  await page.keyboard.up("Shift");
  await expect(box).toHaveValue(/\n$/);
  await page.keyboard.press("Enter");
  await expect(box).toBeFocused();
  await expect(box).toHaveValue("");

  await expect(lastAnswer(page).getByTestId("answer-text")).toContainText("Scripted", { timeout: 30_000 });
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: msg("en", "chat.composer.stop") })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "stopped");
  await expect(box).toBeFocused();
});

test("at 375 px tables and code scroll inside their own box", async ({ page, browser }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await startChat(page, browser);
  expect(await sideways(page)).toBeLessThanOrEqual(0);

  const answer = await askQuestion(page, "[table-demo] show it");
  const table = answer.getByRole("region", { name: msg("en", "chat.table") });
  await expect(table).toBeVisible();
  for (const scroller of [table, answer.locator("pre").first()]) {
    const inner = await scroller.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(inner).toBeGreaterThan(0);
  }
  expect(await sideways(page)).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: msg("en", "chat.openConversations") }).click();
  await expect(page.getByRole("dialog").getByTestId("conversation-list")).toBeVisible();
});

test("a lost connection offers a retry that does not repeat the question", async ({ page, browser }) => {
  await startChat(page, browser);
  await page.route("**/api/v1/chat/stream", (route) => route.abort(), { times: 1 });
  await sendQuestion(page, "What is our total net revenue?");
  const error = page.getByTestId("chat-error");
  await expect(error).toHaveAttribute("data-code", "network_error");

  await error.getByRole("button", { name: msg("en", "chat.retry") }).click();
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done", { timeout: 30_000 });

  await page.reload();
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  await expect(lastAnswer(page)).toHaveAttribute("data-status", "done");
});

test("charts have a table view and survive a reload", async ({ page, browser }) => {
  await startChat(page, browser);
  const answer = await askQuestion(page, "Show a chart of revenue by month");
  const chart = answer.getByTestId("chat-chart");
  await expect(chart).toBeVisible();
  await chart.getByRole("button", { name: msg("en", "charts.viewTable") }).click();
  await expect(chart.getByRole("table")).toBeVisible();

  await page.reload();
  await expect(lastAnswer(page).getByTestId("chat-chart")).toBeVisible();
});

test("an invented number is listed and never shown as verified", async ({ page, browser }) => {
  await startChat(page, browser);
  const answer = await askQuestion(page, "[verifier-demo] what is the net revenue?");
  const warning = answer.getByTestId("verifier-warning");
  await expect(warning).toContainText("9,876,543.21");
  await expect(warning).toContainText("could not be traced");
  await expect(answer.getByTestId("verifier-ok")).toHaveCount(0);
  await expect(answer.getByTestId("answer-text")).not.toContainText("Caveat:");

  await page.reload();
  await expect(lastAnswer(page).getByTestId("verifier-warning")).toContainText("9,876,543.21");
});

test("a conversation can be renamed and deleted", async ({ page, browser }) => {
  await startChat(page, browser);
  await askQuestion(page, "What is our total net revenue?");
  const list = page.getByTestId("conversation-list");
  await list.getByRole("button", { name: /Actions for/ }).first().click();
  await page.getByRole("menuitem", { name: msg("en", "chat.rename") }).click();
  const name = page.getByLabel(msg("en", "chat.renameLabel"), { exact: true });
  await name.fill("Revenue check");
  await page.getByRole("button", { name: msg("en", "chat.renameSave") }).click();
  await expect(list.getByText("Revenue check")).toBeVisible();
  await expect(list.getByRole("button", { name: /Actions for/ }).first()).toBeFocused();

  await list.getByRole("button", { name: /Actions for/ }).first().click();
  await page.getByRole("menuitem", { name: msg("en", "chat.delete") }).click();
  await page.getByRole("button", { name: msg("en", "chat.deleteConfirm") }).click();
  await expect(list.getByText("Revenue check")).toHaveCount(0);
  await expect(page.getByTestId("chat-empty")).toBeVisible();
  await expect(page).not.toHaveURL(/[?&]c=/);
});
