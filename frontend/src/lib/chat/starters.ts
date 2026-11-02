import type { Capabilities } from "@/lib/api/types";

export type StarterKey =
  | "revenue"
  | "forecast"
  | "anomalies"
  | "churn"
  | "whatIf"
  | "segments"
  | "topProducts"
  | "nextStep"
  | "overview"
  | "quality"
  | "reliable";

const MAX_STARTERS = 6;
// Always answerable: they read the file itself, so even a thin dataset gets four questions.
const ALWAYS: StarterKey[] = ["overview", "quality", "reliable", "nextStep"];

/** Choose the example questions this dataset can really answer, most useful first. */
export function selectStarters(caps: Capabilities): StarterKey[] {
  const can = (module: string) => caps.modules.some((m) => m.name === module && m.available);
  const has = (role: string) => role in caps.roles;
  const wanted: [StarterKey, boolean][] = [
    ["revenue", can("kpis")],
    ["forecast", can("forecast")],
    ["anomalies", can("anomalies")],
    ["churn", can("churn")],
    ["whatIf", has("price") && has("quantity")],
    ["segments", can("segmentation")],
    ["topProducts", can("pareto")],
  ];
  const chosen = wanted.filter(([, available]) => available).map(([key]) => key);
  return [...chosen, ...ALWAYS].slice(0, MAX_STARTERS);
}
