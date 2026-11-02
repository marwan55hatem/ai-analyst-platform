import AxeBuilder from "@axe-core/playwright";
import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

export type Locale = "en" | "ar";
type Tree = { [key: string]: string | Tree };

const MESSAGES: Record<Locale, Tree> = {
  en: JSON.parse(readFileSync(path.join(import.meta.dirname, "..", "messages", "en.json"), "utf8")),
  ar: JSON.parse(readFileSync(path.join(import.meta.dirname, "..", "messages", "ar.json"), "utf8")),
};

/** Look up a UI string ("auth.loginTitle") so tests drive the real labels in each language. */
export function msg(locale: Locale, key: string, values: Record<string, string | number> = {}): string {
  const found = key.split(".").reduce<string | Tree | undefined>(
    (node, part) => (node && typeof node === "object" ? node[part] : undefined),
    MESSAGES[locale],
  );
  if (typeof found !== "string") throw new Error(`missing message ${key}`);
  return found.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? `{${name}}`));
}

export const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..");
export const SAMPLE_MESSY = path.join(REPO_ROOT, "evaluation", "datasets", "samples", "retail_messy.csv");

export async function setLocale(context: BrowserContext, locale: Locale, baseURL: string): Promise<void> {
  await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL }]);
}

/** Throwaway demo user; the password is random and never printed. */
export function demoUser(tag: string) {
  const id = `${Date.now().toString(36)}${randomBytes(3).toString("hex")}`;
  return {
    company: `E2E ${tag} ${id}`,
    name: "Demo Analyst",
    email: `e2e-${tag}-${id}@example.com`,
    password: `${randomBytes(9).toString("base64url")}-Tide9`,
  };
}

export async function register(page: Page, locale: Locale, user = demoUser(locale)) {
  await page.goto("/register");
  await page.getByLabel(msg(locale, "auth.company")).fill(user.company);
  await page.getByLabel(msg(locale, "auth.fullName")).fill(user.name);
  await page.getByLabel(msg(locale, "auth.email")).fill(user.email);
  await page.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
  await page.getByRole("button", { name: msg(locale, "auth.registerSubmit") }).click();
  await expect(page).toHaveURL(/\/datasets$/, { timeout: 30_000 });
  return user;
}

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** Run axe on the current page; fail on any violation and keep a JSON record. */
export async function expectNoAxeViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const summary = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5),
  }));
  const dir = path.join(import.meta.dirname, "..", "test-results", "axe");
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    path.join(dir, `${label.replace(/[^a-z0-9-]+/gi, "_")}.json`),
    JSON.stringify({ label, url: page.url(), violations: summary, passes: results.passes.length }, null, 2),
  );
  expect(summary, `axe violations on ${label}`).toEqual([]);
}

/** Current quality score shown by the dial. */
export async function dialScore(page: Page): Promise<number> {
  const dial = page.getByTestId("quality-dial");
  await expect(dial).toBeVisible({ timeout: 180_000 });
  return Number(await dial.getAttribute("data-score"));
}

/** Wait until the latest analysis run on the page reaches a final state. */
export async function waitForAnalysis(page: Page): Promise<string> {
  const progress = page.getByTestId("analysis-progress");
  await expect(progress).toBeVisible({ timeout: 180_000 });
  await expect(progress).toHaveAttribute("data-status", /^(completed|completed_with_warnings|failed)$/, {
    timeout: 300_000,
  });
  return (await progress.getAttribute("data-status")) ?? "";
}

/**
 * Upload the two-year ground-truth file as the signed-in user and wait for its analysis.
 * Returns the dataset id; the dashboard and insights pages pick the newest dataset by default.
 */
export async function uploadAnalysed(page: Page, file: string, locale: Locale = "en"): Promise<string> {
  await page.goto("/datasets/new");
  await page.getByTestId("file-input").setInputFiles(file);
  await page.getByRole("button", { name: msg(locale, "upload.submit") }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  const status = await waitForAnalysis(page);
  expect(status).toMatch(/^completed/);
  return id;
}

export type DemoUser = ReturnType<typeof demoUser>;

let shared: Promise<DemoUser> | null = null;

/**
 * One demo user with the ground-truth dataset analysed, shared by the dashboard, what-if and
 * visual specs. Sign-up is limited to 5 per hour per address, and the suite runs in a single
 * worker, so the user lives in memory for the whole run (never written to disk).
 */
export function analysedUser(browser: Browser, file: string): Promise<DemoUser> {
  shared ??= (async () => {
    const page = await browser.newPage();
    const user = await register(page, "en");
    await uploadAnalysed(page, file);
    await page.close();
    return user;
  })();
  return shared;
}

/** Send a question from the composer (typed into the box, Enter to send). */
export async function sendQuestion(page: Page, question: string, locale: Locale = "en"): Promise<void> {
  const box = page.getByRole("textbox", { name: msg(locale, "chat.composer.label") });
  await box.fill(question);
  await box.press("Enter");
}

/** The answer to the latest question. */
export function lastAnswer(page: Page) {
  return page.getByTestId("answer").last();
}

/**
 * Ask a question and wait for the finished answer. Each answer must carry the scripted model's
 * mark: an answer from a real model fails here, on top of the provider check in global-setup.
 */
export async function askQuestion(page: Page, question: string, locale: Locale = "en") {
  const before = await page.getByTestId("answer").count();
  await sendQuestion(page, question, locale);
  await expect(page.getByTestId("answer")).toHaveCount(before + 1, { timeout: 30_000 });
  const answer = lastAnswer(page);
  await expect(answer).toHaveAttribute("data-status", "done", { timeout: 60_000 });
  await expect(answer.getByTestId("test-model")).toBeVisible();
  return answer;
}

/** Spend the demo company's whole budget for today, in the real table the agent reads. */
export function spendDailyBudget(email: string): void {
  const sql =
    "INSERT INTO agent_usage_daily (id, tenant_id, day, tokens) " +
    "SELECT gen_random_uuid(), tenant_id, (now() AT TIME ZONE 'utc')::date, 2000000000 " +
    `FROM users WHERE email = '${email}' ` +
    "ON CONFLICT (tenant_id, day) DO UPDATE SET tokens = EXCLUDED.tokens";
  const command = `psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "${sql}"`;
  execFileSync("docker", ["compose", "exec", "-T", "postgres", "sh", "-c", command], { cwd: REPO_ROOT, stdio: "pipe" });
}

/** Sign in through the login page in the given language. */
export async function login(page: Page, user: DemoUser, locale: Locale = "en"): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(msg(locale, "auth.email")).fill(user.email);
  await page.getByLabel(msg(locale, "auth.password"), { exact: true }).fill(user.password);
  const submit = page.getByRole("button", { name: msg(locale, "auth.loginSubmit") });
  // Sign-ins are rate limited per address: a spec that signs in many times waits and retries.
  for (let attempt = 0; attempt < 4; attempt++) {
    await submit.click();
    try {
      await expect(page).toHaveURL(/\/datasets$/, { timeout: 15_000 });
      return;
    } catch (error) {
      if (attempt === 3 || (await page.getByRole("alert").count()) === 0) throw error;
      await page.waitForTimeout(12_000);
    }
  }
}

/**
 * Make the viewport as tall as the page and return a function that restores it. Use this instead
 * of `fullPage`: that capture redraws responsive charts at the wrong width, so they spill out of
 * their cards in the image although the page itself is correct.
 */
export async function growToFullPage(page: Page): Promise<() => Promise<void>> {
  const size = page.viewportSize()!;
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: size.width, height: Math.max(height, size.height) });
  await page.waitForTimeout(300);
  return () => page.setViewportSize(size);
}
