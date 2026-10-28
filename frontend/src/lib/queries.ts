"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/lib/api/client";
import type {
  AnalysisRun,
  AnalysisRunListItem,
  CleaningPlan,
  ColumnProfile,
  Dataset,
  DatasetDetail,
  DatasetVersion,
  FixChoice,
  Preview,
  Profile,
  Quality,
  Role,
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
};

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
