// Optimise raw Playwright screenshots into docs/screenshots (palette PNGs, small).
// Usage: SCREENSHOTS=1 npx playwright test screenshots.spec.ts && npm run screenshots
// SCREENSHOT_DIR is where the raw captures are, SCREENSHOT_OUT where they go (replaced if set).
import { mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import sharp from "sharp";

const RAW = process.env.SCREENSHOT_DIR ?? path.join(os.tmpdir(), "analyst-screenshots-raw");
const OUT = process.env.SCREENSHOT_OUT ?? path.resolve(import.meta.dirname, "..", "..", "docs", "screenshots");
const BUDGET = 5 * 1024 * 1024;

mkdirSync(OUT, { recursive: true });
for (const old of readdirSync(OUT).filter((f) => f.endsWith(".png"))) rmSync(path.join(OUT, old));

let total = 0;
for (const file of readdirSync(RAW).filter((f) => f.endsWith(".png")).sort()) {
  const target = path.join(OUT, file);
  const image = sharp(path.join(RAW, file));
  const { height = 0 } = await image.metadata();
  // Very long full-page captures are capped so the folder stays small.
  const capped = height > 3200 ? image.extract({ left: 0, top: 0, width: (await image.metadata()).width ?? 0, height: 3200 }) : image;
  await capped.png({ palette: true, quality: 90, colours: 192, compressionLevel: 9, effort: 10 }).toFile(target);
  total += statSync(target).size;
}
const count = readdirSync(OUT).filter((f) => f.endsWith(".png")).length;
console.log(`${count} screenshots, ${(total / 1024 / 1024).toFixed(2)} MB in ${OUT}`);
if (total > BUDGET) {
  console.error(`over the ${BUDGET / 1024 / 1024} MB budget`);
  process.exit(1);
}
