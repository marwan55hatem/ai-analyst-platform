/**
 * Named schema types plus hand-written types for fields the backend declares as plain
 * `str` or `dict` (statuses, run progress, quality issues). Keep in step with
 * backend/app/models and backend/app/services/analysis.py.
 */
import type { components } from "./schema";

type S = components["schemas"];

export type Me = S["MeResponse"];
export type Dataset = S["DatasetOut"];
export type DatasetDetail = S["DatasetDetail"];
export type DatasetVersion = S["DatasetVersionOut"];
export type ColumnProfile = S["ColumnProfileOut"];
export type Profile = S["ProfileOut"];
export type Preview = S["PreviewOut"];
export type CleaningPlan = S["CleaningPlanOut"];
export type FixChoice = S["FixChoice"];
export type AnalysisRunListItem = S["AnalysisRunListItem"];

export type VersionStatus = "uploaded" | "processing" | "ready" | "failed";
export type ProfileStatus = "pending" | "running" | "ready" | "failed";
export type RunStatus = "pending" | "running" | "completed" | "completed_with_warnings" | "failed";
export type Risk = "low" | "medium" | "high";
export type Confidence = "high" | "medium" | "low";

export type Fix = Omit<S["FixOut"], "risk"> & { risk: Risk };

/** Column roles accepted by PUT /datasets/{id}/columns/{name}/role. */
export const ROLES = [
  "date",
  "revenue",
  "quantity",
  "price",
  "cost",
  "discount",
  "customer_id",
  "order_id",
  "product",
  "category",
  "region",
  "channel",
  "status",
  "email",
  "phone",
  "churn_flag",
  "other",
] as const;
export type Role = (typeof ROLES)[number];

/** Analysis modules in run order (backend/app/ml/registry.py). */
export const MODULES = [
  "kpis",
  "trend_seasonality",
  "pareto",
  "cohort_retention",
  "forecast",
  "anomalies",
  "segmentation",
  "churn",
  "drivers",
] as const;
export type ModuleName = (typeof MODULES)[number];

export type ModuleState =
  | { status: "completed"; insights: number; warnings: string[]; notes?: string[] }
  | { status: "skipped"; reason: string; insights: number; warnings: string[] }
  | { status: "failed"; error: string; insights: number; warnings: string[] };

export interface RunProgress {
  total: number;
  done: number;
  modules: Partial<Record<string, ModuleState>>;
}

export type AnalysisRun = Omit<S["AnalysisRunOut"], "status" | "progress"> & {
  status: RunStatus;
  progress: Partial<RunProgress>;
};

export type QualityDimension =
  | "completeness"
  | "validity"
  | "uniqueness"
  | "consistency"
  | "sufficiency";

export const DIMENSIONS: QualityDimension[] = [
  "completeness",
  "validity",
  "uniqueness",
  "consistency",
  "sufficiency",
];

export interface QualityIssue {
  code: string;
  title: string;
  detail: string;
  columns: string[];
  points_lost: number;
  dimension: QualityDimension;
  sample_size: number;
  confidence: Confidence;
  suggested_fix: string;
  fix_id: string | null;
}

export type Quality = Omit<S["QualityOut"], "issues" | "confidence"> & {
  issues: QualityIssue[];
  confidence: Confidence;
};

export function isRunActive(status: string | undefined): boolean {
  return status === "pending" || status === "running";
}
