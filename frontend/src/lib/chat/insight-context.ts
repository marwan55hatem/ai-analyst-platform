import type { Dataset, Insight } from "@/lib/api/types";

export type InsightContext =
  | { kind: "none" | "loading" | "older" | "missing" }
  | { kind: "ready"; title: string }
  | { kind: "switch"; datasetId: string };

/**
 * Where a ?insight=ID link stands. A chat answers about one dataset, so a finding from another
 * dataset moves the chat there, and one from an earlier version is not offered at all: the
 * analyst would answer from different data than the finding came from.
 */
export function insightContext({
  id,
  insight,
  failed = false,
  datasets,
  activeId,
}: {
  id: string | null;
  insight?: Insight;
  failed?: boolean;
  datasets: readonly Dataset[];
  activeId: string;
}): InsightContext {
  if (!id) return { kind: "none" };
  if (failed) return { kind: "missing" };
  if (!insight) return { kind: "loading" };
  const owner = datasets.find((d) => d.latest_version?.id === insight.dataset_version_id);
  if (!owner) return { kind: "older" };
  return owner.id === activeId ? { kind: "ready", title: insight.title } : { kind: "switch", datasetId: owner.id };
}

/** The composer's text once the finding is removed: only an untouched question goes with it. */
export function removePrefill(draft: string, prefill: string): string {
  return draft === prefill ? "" : draft;
}
