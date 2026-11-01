import { describe, expect, it } from "vitest";

import type { Dataset, Insight } from "@/lib/api/types";

import { insightContext, removePrefill } from "./insight-context";

const dataset = (id: string, versionId: string) => ({ id, latest_version: { id: versionId } }) as unknown as Dataset;
const DATASETS = [dataset("d1", "v1"), dataset("d2", "v2")];
const insight = { id: "i", title: "Revenue fell", dataset_version_id: "v1" } as Insight;

describe("insightContext", () => {
  it("is empty without an id", () => {
    expect(insightContext({ id: null, datasets: DATASETS, activeId: "d1" })).toEqual({ kind: "none" });
  });

  it("waits while the insight loads", () => {
    expect(insightContext({ id: "i", datasets: DATASETS, activeId: "d1" })).toEqual({ kind: "loading" });
  });

  it("is ready when the insight belongs to the active dataset", () => {
    expect(insightContext({ id: "i", insight, datasets: DATASETS, activeId: "d1" })).toEqual({
      kind: "ready",
      title: "Revenue fell",
    });
  });

  it("asks to switch when it belongs to another dataset", () => {
    expect(insightContext({ id: "i", insight, datasets: DATASETS, activeId: "d2" })).toEqual({
      kind: "switch",
      datasetId: "d1",
    });
  });

  it("is older when no dataset's latest version has it", () => {
    const old = { ...insight, dataset_version_id: "v0" };
    expect(insightContext({ id: "i", insight: old, datasets: DATASETS, activeId: "d1" })).toEqual({ kind: "older" });
  });

  it("is missing when it could not be loaded", () => {
    expect(insightContext({ id: "i", failed: true, datasets: DATASETS, activeId: "d1" })).toEqual({ kind: "missing" });
  });
});

describe("removePrefill", () => {
  it("clears a question the user did not touch", () => {
    expect(removePrefill("About the finding", "About the finding")).toBe("");
  });

  it("keeps a question the user edited", () => {
    expect(removePrefill("About the finding, and more", "About the finding")).toBe("About the finding, and more");
  });
});
