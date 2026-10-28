import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/** Folder (in the OS temp dir) holding the generated messy file and other fixtures. */
export const FIXTURE_DIR = path.join(os.tmpdir(), "analyst-e2e-fixtures");
export const GENERATED_MESSY = path.join(FIXTURE_DIR, "retail_gen_messy_en.csv");

/** Generate the messy retail file with the evaluation generator (deterministic seed). */
export default function globalSetup(): void {
  if (existsSync(GENERATED_MESSY)) return;
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const repo = path.resolve(import.meta.dirname, "..", "..");
  execFileSync(
    "uv",
    ["run", "--directory", "backend", "python", "../evaluation/datasets/generators/retail_generator.py", FIXTURE_DIR],
    { cwd: repo, stdio: "inherit" },
  );
}
