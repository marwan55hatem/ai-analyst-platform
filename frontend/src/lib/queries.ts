"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type {
  AiUsage,
  AnalysisRun,
  AnalysisRunListItem,
  Anomalies,
  BusinessProfile,
  Capabilities,
  Churn,
  ChurnSort,
  CleaningPlan,
  ColumnProfile,
  Conversation,
  Dataset,
  DatasetDetail,
  DatasetVersion,
  Drivers,
  FixChoice,
  Grain,
  Insight,
  InsightSummary,
  Message,
  Preview,
  Profile,
  Quality,
  Role,
  ScenarioIn,
  Segments,
  SegmentName,
  SegmentSort,
  SortOrder,
  TimeSeries,
  WhatIfResult,
} from "@/lib/api/types";
import { isRunActive } from "@/lib/api/types";

const POLL_MS = 1500;

export const keys = {
  datasets: ["datasets"] as const,
  dataset: (id: string) => ["dataset", id] as const,
  lastRun: (id: string) => ["dataset", id, "last-run"] as const,
  versionData: (id: string, version: number, what: string) =>
    ["dataset", id, "v", version, what] as const,
  runs: (id: string, version: number) => ["dataset", id, "v", version, "runs"] as const,
  run: (runId: string) => ["run", runId] as const,
  runData: (runId: string, what: string, params: object = {}) =>
    ["run", runId, what, params] as const,
  insight: (id: string) => ["insight", id] as const,
  conversations: (datasetId: string) => ["conversations", datasetId] as const,
  messages: (id: string) => ["conversation", id, "messages"] as const,
  usage: ["ai-usage"] as const,
  profile: ["company-profile"] as const,
};

/** Results of a finished run do not change, so they stay fresh for the session. */
const RUN_STALE_MS = 5 * 60_000;

/** Whether a version is still being read or checked by the worker. */
export function isVersionBusy(v: DatasetVersion | null | undefined): boolean {
  if (!v) return false;
  if (v.status === "uploaded" || v.status === "processing") return true;
  return v.status === "ready" && (v.profile_status === "pending" || v.profile_status === "running");
}

export function useDatasets() {
  return useQuery({
    queryKey: keys.datasets,
    queryFn: () =>
      unwrap(api.GET("/api/v1/datasets", { params: { query: { limit: 200 } } })) as Promise<
        Dataset[]
      >,
    refetchInterval: (query) =>
      query.state.data?.some((d) => isVersionBusy(d.latest_version)) ? POLL_MS * 2 : false,
  });
}

export function useDataset(id: string) {
  return useQuery({
    queryKey: keys.dataset(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}", { params: { path: { dataset_id: id } } }),
      ) as Promise<DatasetDetail>,
    refetchInterval: (query) => (isVersionBusy(query.state.data?.latest_version) ? POLL_MS : false),
  });
}

/** Most recent analysis run of a dataset (any version), for list pages. */
export function useLastRun(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.lastRun(id),
    enabled,
    queryFn: async () => {
      const runs = await unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/analysis-runs", {
          params: { path: { dataset_id: id }, query: { limit: 1 } },
        }),
      );
      return (runs[0] ?? null) as AnalysisRunListItem | null;
    },
  });
}

function versionQuery(id: string, version: number) {
  return { params: { path: { dataset_id: id }, query: { version } } };
}

export function useQuality(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, "quality"),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/quality", versionQuery(id, version)),
      ).then((q) => q as unknown as Quality),
  });
}

export function useProfile(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, "profile"),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/profile", versionQuery(id, version)),
      ) as Promise<Profile>,
  });
}

export function useCleaningPlan(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, "plan"),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/cleaning-plan", versionQuery(id, version)),
      ) as Promise<CleaningPlan>,
  });
}

export function usePreview(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, "preview"),
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/preview", {
          params: { path: { dataset_id: id }, query: { rows: 50 } },
        }),
      ) as Promise<Preview>,
  });
}

/** Latest analysis run of one version; polls while it is queued or running. */
export function useLatestRun(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.runs(id, version),
    enabled,
    queryFn: async () => {
      const runs = await unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/analysis-runs", {
          params: { path: { dataset_id: id }, query: { version, limit: 1 } },
        }),
      );
      const latest = runs[0];
      if (!latest) return null;
      return (await unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}", { params: { path: { run_id: latest.id } } }),
      )) as AnalysisRun;
    },
    refetchInterval: (query) => (isRunActive(query.state.data?.status) ? POLL_MS : false),
  });
}

export function useSetRole(id: string, version: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ column, role }: { column: string; role: Role }) =>
      unwrap(
        api.PUT("/api/v1/datasets/{dataset_id}/columns/{name}/role", {
          params: { path: { dataset_id: id, name: column }, query: { version } },
          body: { role },
        }),
      ) as Promise<ColumnProfile>,
    onSuccess: (updated) => {
      client.setQueryData<Profile>(keys.versionData(id, version, "profile"), (old) =>
        old
          ? { ...old, columns: old.columns.map((c) => (c.name === updated.name ? updated : c)) }
          : old,
      );
    },
  });
}

export function useApplyPlan(id: string, version: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (fixes: FixChoice[]) =>
      unwrap(
        api.POST("/api/v1/datasets/{dataset_id}/cleaning-plan/apply", {
          params: { path: { dataset_id: id }, query: { version } },
          body: { fixes },
        }),
      ) as Promise<DatasetDetail>,
    onSuccess: (detail) => {
      client.setQueryData(keys.dataset(id), detail);
      void client.invalidateQueries({ queryKey: keys.dataset(id) });
      void client.invalidateQueries({ queryKey: keys.datasets });
    },
  });
}

export function useStartAnalysis(id: string, version: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/datasets/{dataset_id}/analyze", {
          params: { path: { dataset_id: id }, query: { version } },
        }),
      ) as Promise<AnalysisRun>,
    onSuccess: (run) => {
      client.setQueryData(keys.runs(id, version), run);
      void client.invalidateQueries({ queryKey: keys.lastRun(id) });
    },
    onSettled: () => client.invalidateQueries({ queryKey: keys.runs(id, version) }),
  });
}

export function useRerunProfile(id: string, version: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/datasets/{dataset_id}/profile/rerun", versionQuery(id, version)),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.dataset(id) }),
  });
}

export function useCapabilities(id: string, version: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, "capabilities"),
    enabled,
    staleTime: RUN_STALE_MS,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/capabilities", versionQuery(id, version)),
      ) as Promise<Capabilities>,
  });
}

export interface RangeQuery {
  start?: string;
  end?: string;
  grain: Grain;
}

/** KPIs for a date range vs the window before it, and the series (computed by the backend). */
export function useTimeseries(id: string, version: number, range: RangeQuery, enabled: boolean) {
  return useQuery({
    queryKey: keys.versionData(id, version, `ts:${range.start}:${range.end}:${range.grain}`),
    enabled,
    staleTime: RUN_STALE_MS,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/datasets/{dataset_id}/timeseries", {
          params: { path: { dataset_id: id }, query: { version, ...range } },
        }),
      ) as Promise<TimeSeries>,
  });
}

export interface InsightFilters {
  module?: string;
  confidence?: "high" | "medium" | "low";
  types?: string[];
}

export const INSIGHT_PAGE = 20;

/** Insights of a run, highest impact first, one server page at a time. */
export function useInsights(runId: string | undefined, filters: InsightFilters = {}) {
  return useInfiniteQuery({
    queryKey: keys.runData(runId ?? "", "insights", filters),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/insights", {
          params: {
            path: { run_id: runId ?? "" },
            query: { ...filters, limit: INSIGHT_PAGE, offset: pageParam },
          },
        }),
      ) as Promise<InsightSummary[]>,
    getNextPageParam: (last, pages) =>
      last.length === INSIGHT_PAGE ? pages.length * INSIGHT_PAGE : undefined,
  });
}

/** Insights of one module of a run (small lists such as the Pareto dimensions). */
export function useModuleInsights(runId: string | undefined, module: string) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "module", { module }),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    queryFn: async () => {
      const list = await unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/insights", {
          params: { path: { run_id: runId ?? "" }, query: { module, limit: 50 } },
        }),
      );
      return Promise.all(
        list.map(
          (i) =>
            unwrap(
              api.GET("/api/v1/insights/{insight_id}", {
                params: { path: { insight_id: i.id } },
              }),
            ) as Promise<Insight>,
        ),
      );
    },
  });
}

export function useInsight(id: string | null) {
  return useQuery({
    queryKey: keys.insight(id ?? ""),
    enabled: Boolean(id),
    staleTime: RUN_STALE_MS,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/insights/{insight_id}", { params: { path: { insight_id: id ?? "" } } }),
      ) as Promise<Insight>,
  });
}

export function useForecasts(runId: string | undefined) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "forecast"),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    queryFn: async () => {
      const found = await unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/forecast", {
          params: { path: { run_id: runId ?? "" } },
        }),
      );
      return { ...found, forecasts: found.forecasts as unknown as Insight[] };
    },
  });
}

export function useAnomalies(runId: string | undefined) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "anomalies"),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/anomalies", {
          params: { path: { run_id: runId ?? "" } },
        }),
      ) as Promise<Anomalies>,
  });
}

export function useDrivers(runId: string | undefined) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "drivers"),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/drivers", {
          params: { path: { run_id: runId ?? "" } },
        }),
      ) as Promise<Drivers>,
  });
}

export interface SegmentQuery {
  segment?: SegmentName;
  cluster?: number;
  sort: SegmentSort;
  order: SortOrder;
  limit: number;
  offset: number;
}

export function useSegments(runId: string | undefined, query: SegmentQuery) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "segments", query),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/segments", {
          params: { path: { run_id: runId ?? "" }, query },
        }),
      ) as Promise<Segments>,
  });
}

export interface ChurnQuery {
  risk_band?: "high" | "medium" | "low";
  rank_band?: "top_10pct" | "rest";
  sort: ChurnSort;
  order: SortOrder;
  limit: number;
  offset: number;
}

export function useChurn(runId: string | undefined, query: ChurnQuery) {
  return useQuery({
    queryKey: keys.runData(runId ?? "", "churn", query),
    enabled: Boolean(runId),
    staleTime: RUN_STALE_MS,
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/analysis-runs/{run_id}/churn", {
          params: { path: { run_id: runId ?? "" }, query },
        }),
      ) as Promise<Churn>,
  });
}

export function useWhatIf(id: string, version: number) {
  return useMutation({
    mutationFn: (scenarios: ScenarioIn[]) =>
      unwrap(
        api.POST("/api/v1/datasets/{dataset_id}/what-if", {
          params: { path: { dataset_id: id }, query: { version } },
          body: { scenarios },
        }),
      ) as unknown as Promise<WhatIfResult>,
  });
}

/** The signed-in user's conversations about one dataset, most recent first. */
export function useConversations(datasetId: string | undefined) {
  return useQuery({
    queryKey: keys.conversations(datasetId ?? ""),
    enabled: Boolean(datasetId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/conversations", {
          params: { query: { mine: true, dataset_id: datasetId, limit: 100 } },
        }),
      ) as Promise<Conversation[]>,
  });
}

export function fetchMessages(id: string): Promise<Message[]> {
  return unwrap(
    api.GET("/api/v1/conversations/{conversation_id}/messages", {
      params: { path: { conversation_id: id }, query: { limit: 500 } },
    }),
  );
}

export function useMessages(id: string | null) {
  return useQuery({
    queryKey: keys.messages(id ?? ""),
    enabled: Boolean(id),
    queryFn: () => fetchMessages(id ?? ""),
  });
}

export function useCreateConversation(datasetId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/conversations", { body: { dataset_id: datasetId } })) as Promise<Conversation>,
    onSuccess: () => client.invalidateQueries({ queryKey: keys.conversations(datasetId) }),
  });
}

export function useRenameConversation(datasetId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      unwrap(
        api.PATCH("/api/v1/conversations/{conversation_id}", {
          params: { path: { conversation_id: id } },
          body: { title },
        }),
      ) as Promise<Conversation>,
    onSuccess: () => client.invalidateQueries({ queryKey: keys.conversations(datasetId) }),
  });
}

export function useDeleteConversation(datasetId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/conversations/{conversation_id}", {
          params: { path: { conversation_id: id } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.conversations(datasetId) }),
  });
}

/** Today's AI usage; fresh on every visit, because any teammate can spend it. */
export function useAiUsage() {
  return useQuery({
    queryKey: keys.usage,
    queryFn: () => unwrap(api.GET("/api/v1/ai/usage")) as Promise<AiUsage>,
  });
}

export function useCompanyProfile() {
  return useQuery({
    queryKey: keys.profile,
    queryFn: () => unwrap(api.GET("/api/v1/company/profile")) as Promise<BusinessProfile>,
  });
}
