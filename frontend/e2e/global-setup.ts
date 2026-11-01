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
const REPO = path.resolve(import.meta.dirname, "..", "..");

/** The LLM provider a running service really uses (its name only, never a key). */
function providerOf(service: string): string {
  const read = "from app.core.config import get_settings; print(get_settings().llm_provider)";
  return execFileSync("docker", ["compose", "exec", "-T", service, "python", "-c", read], {
    cwd: REPO,
    encoding: "utf8",
  }).trim();
}

/**
 * Tests never spend real model tokens: the run stops here unless the stack uses the scripted
 * model. LIVE_SMOKE=1 is the one exception, for the single question of live-smoke.spec.ts, and
 * even then the worker stays on the scripted model so an upload cannot spend tokens.
 */
function refuseLiveModel(): void {
  const expected = { api: process.env.LIVE_SMOKE ? "groq" : "fake", worker: "fake" };
  for (const [service, provider] of Object.entries(expected)) {
    const found = providerOf(service);
    if (found !== provider) {
      throw new Error(`The ${service} uses LLM_PROVIDER=${found}; this e2e run needs ${provider}. Stopping before any model call.`);
    }
  }
}

/** Generate the fixture files with the evaluation generators (deterministic seeds). */
export default function globalSetup(): void {
  refuseLiveModel();
  mkdirSync(FIXTURE_DIR, { recursive: true });
  const run = (script: string, ...args: string[]) =>
    execFileSync("uv", ["run", "--directory", "backend", "python", `${GENERATORS}/${script}`, FIXTURE_DIR, ...args], {
      cwd: REPO,
      stdio: "inherit",
    });
  if (!existsSync(GENERATED_MESSY)) run("retail_generator.py");
  if (!existsSync(GROUND_TRUTH)) {
    run("ground_truth_generator.py", "--years", "2", "--anomalies", "--churn", "--elasticity", "--drivers");
  }
}
