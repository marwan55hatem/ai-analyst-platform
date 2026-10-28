import AxeBuilder from "@axe-core/playwright";
import { expect, type BrowserContext, type Page } from "@playwright/test";
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
