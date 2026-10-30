import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

/** Folder (in the OS temp dir) holding the generated messy file and other fixtures. */
export const FIXTURE_DIR = path.join(os.tmpdir(), "analyst-e2e-fixtures");
export const GENERATED_MESSY = path.join(FIXTURE_DIR, "retail_gen_messy_en.csv");
/** Two years of daily sales with planted anomalies, churn, price elasticity and drivers. */
export const GROUND_TRUTH = path.join(FIXTURE_DIR, "ground_truth_en_anomalies_churn_elasticity_drivers.csv");

const GENERATORS = "../evaluation/datasets/generators";

/** Generate the fixture files with the evaluation generators (deterministic seeds). */
export default function globalSetup(): void {
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const repo = path.resolve(import.meta.dirname, "..", "..");
  const run = (script: string, ...args: string[]) =>
    execFileSync("uv", ["run", "--directory", "backend", "python", `${GENERATORS}/${script}`, FIXTURE_DIR, ...args], {
      cwd: repo,
      stdio: "inherit",
    });
  if (!existsSync(GENERATED_MESSY)) run("retail_generator.py");
  if (!existsSync(GROUND_TRUTH)) {
    run("ground_truth_generator.py", "--years", "2", "--anomalies", "--churn", "--elasticity", "--drivers");
  }
}
