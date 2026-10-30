"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import type { Dataset } from "@/lib/api/types";
import { useDatasets, useLatestRun } from "@/lib/queries";
import { useUrlUpdate } from "@/lib/url-state";

/** Datasets whose latest version can be analysed (newest first, as the API lists them). */
export function analysable(datasets: readonly Dataset[] | undefined): Dataset[] {
  return (datasets ?? []).filter(
    (d) => d.latest_version?.status === "ready" && d.latest_version.profile_status === "ready",
  );
}

/**
 * The dataset every dashboard and insights page shows. It lives in the URL (?dataset=ID) so a
 * link opens the same data; without it the newest analysable dataset is used.
 */
export function useActiveDataset() {
  const params = useSearchParams();
  const update = useUrlUpdate();
  const list = useDatasets();
  const datasets = useMemo(() => analysable(list.data), [list.data]);
  const wanted = params.get("dataset");
  const dataset = datasets.find((d) => d.id === wanted) ?? datasets[0];
  const version = dataset?.latest_version?.version ?? 0;
  const run = useLatestRun(dataset?.id ?? "", version, Boolean(dataset));

  const select = useCallback(
    (id: string) => update({ dataset: id, start: null, end: null, range: null, insight: null }),
    [update],
  );

  return {
    datasets,
    dataset,
    version,
    run: run.data ?? null,
    loading: list.isPending || (Boolean(dataset) && run.isPending),
    error: list.error ?? run.error,
    retry: () => {
      void list.refetch();
      void run.refetch();
    },
    select,
  };
}

export type ActiveDataset = ReturnType<typeof useActiveDataset>;

/** Keep the dataset parameter when linking between dashboard and insights pages. */
export function withDataset(href: string, datasetId: string | undefined): string {
  if (!datasetId) return href;
  const [path, query = ""] = href.split("?");
  const next = new URLSearchParams(query);
  next.set("dataset", datasetId);
  return `${path}?${next.toString()}`;
}
