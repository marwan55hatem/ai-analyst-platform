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
export type Conversation = S["ConversationOut"];
export type Message = S["MessageOut"];
export type AiUsage = S["AiUsageOut"];
export type BusinessProfile = S["BusinessProfileOut"];

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

/* ---- Step 12: dashboard and insights ---------------------------------------------------- */

export type TimeSeries = S["TimeSeriesOut"];
export type Kpi = S["KpiOut"];
export type KpiKey = Kpi["key"];
export type SeriesPoint = S["SeriesPoint"];
export type Grain = TimeSeries["grain"];
export type Capabilities = S["CapabilitiesOut"];
export type InsightSummary = S["InsightSummary"];
export type Anomalies = S["AnomaliesOut"];
export type Segments = S["SegmentsOut"];
export type SegmentAssignment = S["SegmentAssignmentOut"];
export type Churn = S["ChurnOut"];
export type ChurnScore = S["ChurnScoreOut"];
export type Drivers = S["DriversOut"];
export type ScenarioIn = S["ScenarioIn"];
export type Lever = ScenarioIn["lever"];
export type Scope = NonNullable<ScenarioIn["scope"]>;

export const SEGMENT_NAMES = [
  "Champions",
  "Loyal",
  "Potential loyalists",
  "New",
  "At risk",
  "Hibernating",
  "Lost",
] as const;
export type SegmentName = (typeof SEGMENT_NAMES)[number];
export type SegmentSort = "customer_key" | "monetary" | "frequency" | "recency_days";
export type ChurnSort =
  | "probability"
  | "revenue_at_stake"
  | "expected_loss"
  | "recency_days"
  | "monetary";
export type SortOrder = "asc" | "desc";

/** One SQL query behind an insight and the first rows of its result. */
export interface EvidenceQuery {
  purpose: string;
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

/** An insight with its payload; `payload` keys depend on the module (see backend/app/ml). */
export type Insight = Omit<S["InsightOut"], "payload" | "evidence"> & {
  payload: InsightPayload;
  evidence: { queries?: EvidenceQuery[] };
};

export interface InsightPayload {
  value?: unknown;
  method?: string;
  warnings?: string[];
  money_effect?: number;
  [key: string]: unknown;
}

export interface ParetoItem {
  item: string;
  revenue: number;
  share_pct: number;
  cumulative_pct: number;
  abc: "A" | "B" | "C";
  returns: number;
}

export interface ForecastScore {
  model: string;
  mase: number | null;
  smape: number | null;
  mae: number | null;
  coverage80: number | null;
  coverage95: number | null;
  folds: number;
}

/** chart_spec of a forecast insight ("forecast_band"). */
export interface ForecastSpec {
  type: "forecast_band";
  unit: "day" | "week" | "month" | "quarter";
  title: string;
  history: { period: string; value: number }[];
  forecast: {
    period: string;
    point: number;
    lo80: number;
    hi80: number;
    lo95: number;
    hi95: number;
  }[];
}

export interface AnomalyEvent {
  start: string;
  end: string;
  kind: "spike" | "drop";
  actual: number;
  expected: number;
  change: number;
  peak_z: number;
  length: number;
  insight_id: string | null;
}

export interface Band {
  low: number;
  expected: number;
  high: number;
}

/** One answered what-if scenario (backend/app/ml/whatif/scenarios.py). */
export interface ScenarioResult {
  status: "ok" | "not_enough_evidence";
  lever: Lever;
  change_pct: number;
  scope: Scope;
  target: string | null;
  baseline?: { from: string; to: string; months: number; yearly_revenue: number } | null;
  revenue_change: Band | null;
  profit_change: Band | null;
  units_change_pct?: Band | null;
  assumptions: string[];
  warnings: string[];
  extrapolation_risk?: boolean;
  confidence: Confidence;
  sample_size?: number;
  summary: string;
  evidence?: EvidenceQuery[];
}

export interface WhatIfResult {
  scenarios: ScenarioResult[];
  note: string;
}
