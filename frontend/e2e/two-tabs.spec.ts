import { expect, test, type Page } from "@playwright/test";

import { msg, register } from "./helpers";

interface Span {
  tab: string;
  start: number;
  end: number;
  status: number;
}

function watchRefreshes(page: Page, tab: string, spans: Span[]) {
  const started = new Map<object, number>();
  page.on("request", (r) => {
    if (r.url().endsWith("/api/v1/auth/refresh")) started.set(r, Date.now());
  });
  page.on("response", (r) => {
    const req = r.request();
    if (req.url().endsWith("/api/v1/auth/refresh")) {
      spans.push({ tab, start: started.get(req) ?? 0, end: Date.now(), status: r.status() });
    }
  });
}

test("two tabs refreshing at the same time stay signed in", async ({ context }) => {
  const first = await context.newPage();
  await register(first, "en");
  const second = await context.newPage();
  await second.goto("/datasets");
  await expect(second.getByRole("heading", { level: 1, name: msg("en", "datasets.title") })).toBeVisible();

  const spans: Span[] = [];
  watchRefreshes(first, "A", spans);
  watchRefreshes(second, "B", spans);

  // Both tabs restore their session at the same moment, three times over.
  for (let round = 0; round < 3; round += 1) {
    await Promise.all([first.reload(), second.reload()]);
    for (const page of [first, second]) {
      await expect(page.getByRole("heading", { level: 1, name: msg("en", "datasets.title") })).toBeVisible();
    }
  }

  expect(spans.length).toBe(6);
  expect(spans.every((s) => s.status === 200)).toBe(true);
  // The Web Lock serialises them: no two refresh requests overlap in time.
  const sorted = [...spans].sort((a, b) => a.start - b.start);
  for (let i = 1; i < sorted.length; i += 1) {
    expect(sorted[i]!.start, "refreshes must not overlap").toBeGreaterThanOrEqual(sorted[i - 1]!.end - 5);
  }
  // Still signed in afterwards in both tabs.
  for (const page of [first, second]) {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { level: 1, name: msg("en", "settings.title") })).toBeVisible();
  }
});
