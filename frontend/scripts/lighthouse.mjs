// Lighthouse scores for every page in English and Arabic: accessibility must reach 90;
// performance and best practices are reported.
// Usage (stack running): npm run lighthouse   [BASE_URL=http://localhost:3000] [LIGHTHOUSE_SAMPLE=file.csv]
// Uses Playwright's Chromium; signs in once, then audits each page in the same browser so
// the session is restored from the refresh cookie (storage reset is disabled).
import { chromium } from "@playwright/test";
import lighthouse from "lighthouse";
import { randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ROOT = path.resolve(import.meta.dirname, "..");
// Two years of daily sales from the e2e fixtures when present (npx playwright test makes it),
// so the dashboard and insights pages are audited with real charts; else the small sample.
const GROUND_TRUTH = path.join(os.tmpdir(), "analyst-e2e-fixtures", "ground_truth_en_anomalies_churn_elasticity_drivers.csv");
const SAMPLE =
  process.env.LIGHTHOUSE_SAMPLE ??
  (existsSync(GROUND_TRUTH) ? GROUND_TRUTH : path.join(ROOT, "..", "evaluation", "datasets", "samples", "retail_messy.csv"));
const OUT = path.join(ROOT, ".lighthouse");

async function api(pathname, init = {}) {
  const res = await fetch(`${BASE}${pathname}`, init);
  if (!res.ok) throw new Error(`${pathname} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
}

/** Throwaway user with one uploaded dataset; credentials stay in memory. */
async function prepare() {
  const id = `${Date.now().toString(36)}${randomBytes(3).toString("hex")}`;
  const user = { email: `lh-${id}@example.com`, password: `${randomBytes(9).toString("base64url")}-Tide9` };
  const json = { "Content-Type": "application/json" };
  await api("/api/v1/auth/register", {
    method: "POST",
    headers: json,
    body: JSON.stringify({ company_name: `Lighthouse ${id}`, full_name: "Demo Analyst", ...user }),
  });
  const { access_token: token } = await api("/api/v1/auth/login", { method: "POST", headers: json, body: JSON.stringify(user) });
  const form = new FormData();
  form.append("file", new Blob([readFileSync(SAMPLE)]), "retail_messy.csv");
  form.append("name", "retail_messy");
  const dataset = await api("/api/v1/datasets", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
  for (let i = 0; i < 300; i += 1) {
    const d = await api(`/api/v1/datasets/${dataset.id}`, { headers: { Authorization: `Bearer ${token}` } });
    if (d.latest_version?.profile_status === "ready") break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  // Wait for the automatic analysis so the insights pages have results to audit.
  for (let i = 0; i < 300; i += 1) {
    const runs = await api(`/api/v1/datasets/${dataset.id}/analysis-runs`, { headers: { Authorization: `Bearer ${token}` } });
    if (runs[0] && !["pending", "running"].includes(runs[0].status)) break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return { user, detail: `/datasets/${dataset.id}` };
}

async function main() {
  const { user, detail } = await prepare();
  const browser = await puppeteer.launch({ executablePath: chromium.executablePath(), headless: true });
  const results = [];
  for (const locale of ["en", "ar"]) {
    // A fresh context per language: signed out, with only the language cookie.
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    await context.setCookie({ name: "NEXT_LOCALE", value: locale, domain: new URL(BASE).hostname, path: "/" });
    for (const url of ["/login", "/register"]) results.push(await audit(page, url, locale));
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
    await page.type('input[type="email"]', user.email);
    await page.type('input[autocomplete="current-password"]', user.password);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle0" }), page.click('button[type="submit"]')]);
    const pages = ["/datasets", "/datasets/new", detail, "/dashboard", "/insights", "/insights/forecast", "/insights/segments", "/insights/churn", "/insights/what-if", "/chat", "/reports", "/settings"];
    for (const url of pages) {
      results.push(await audit(page, url, locale));
    }
    await context.close();
  }
  await browser.close();
  mkdirSync(OUT, { recursive: true });
  writeFileSync(path.join(OUT, "accessibility.json"), JSON.stringify(results, null, 2));
  for (const r of results) {
    const scores = `a11y ${String(r.score).padStart(3)}  perf ${String(r.performance).padStart(3)}  best ${String(r.bestPractices).padStart(3)}`;
    console.log(`${r.score >= 90 ? "PASS" : "FAIL"}  ${scores}  ${r.locale}  ${r.page}${r.failed.length ? `  (${r.failed.join(", ")})` : ""}`);
  }
  if (results.some((r) => r.score < 90)) process.exit(1);
}

async function audit(page, url, locale) {
  const { lhr } = await lighthouse(
    `${BASE}${url}`,
    { output: "json", logLevel: "error", onlyCategories: ["accessibility", "performance", "best-practices"], disableStorageReset: true, formFactor: "desktop", screenEmulation: { disabled: true } },
    undefined,
    page,
  );
  const failed = Object.values(lhr.audits)
    .filter((a) => a.score === 0 && a.scoreDisplayMode === "binary")
    .map((a) => a.id);
  const pageName = url.startsWith("/datasets/") && url.length > 20 ? "/datasets/[id]" : url;
  const percent = (category) => Math.round((lhr.categories[category].score ?? 0) * 100);
  return {
    page: pageName,
    locale,
    score: percent("accessibility"),
    performance: percent("performance"),
    bestPractices: percent("best-practices"),
    failed,
  };
}

await main();
