"use client";

import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { RangeBar } from "@/components/charts/range-bar";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { useActiveDataset } from "@/lib/active-dataset";
import type { Lever, ScenarioIn, ScenarioResult, Scope } from "@/lib/api/types";
import { byDimension, itemsOf } from "@/lib/charts/pareto";
import { clampChange, isComplete, MAX_CHANGE, MAX_SCENARIOS, sharedDomain } from "@/lib/charts/whatif";
import { useFormat } from "@/lib/format";
import { useModuleInsights, useWhatIf } from "@/lib/queries";

import { AnalysisGate, DatasetPicker } from "./analysis-gate";
import { ConfidenceBadge, EngineText } from "./badges";
import { EvidenceBlock } from "./insight-drawer";

const LEVERS: Lever[] = ["price", "discount", "volume", "cost"];
const SCOPES: Scope[] = ["all", "category", "product"];
const START: ScenarioIn = { lever: "price", change_pct: 5, scope: "all", target: null };

/** /insights/what-if: compare up to three lever changes; every answer is a range. */
export function WhatIfPage() {
  const t = useTranslations("whatIf");
  const active = useActiveDataset();
  return (
    <>
      <PageHeader title={t("title")} description={t("description")} actions={<DatasetPicker active={active} />} />
      <AnalysisGate active={active}>
        {(run) =>
          active.dataset ? (
            <WhatIfView key={active.dataset.id} runId={run.id} datasetId={active.dataset.id} version={active.version} />
          ) : null
        }
      </AnalysisGate>
    </>
  );
}

function WhatIfView({ runId, datasetId, version }: { runId: string; datasetId: string; version: number }) {
  const t = useTranslations("whatIf");
  const [scenarios, setScenarios] = useState<ScenarioIn[]>([START]);
  const mutation = useWhatIf(datasetId, version);
  const pareto = useModuleInsights(runId, "pareto");
  const targets = useMemo(() => {
    const dims = byDimension(pareto.data ?? []);
    return {
      category: itemsOf(dims.category).map((i) => i.item),
      product: itemsOf(dims.product).map((i) => i.item),
    };
  }, [pareto.data]);
  const ready = scenarios.every(isComplete);
  const update = (i: number, patch: Partial<ScenarioIn>) =>
    setScenarios((list) => list.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  return (
    <div className="space-y-8">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) mutation.mutate(scenarios.map((s) => ({ ...s, target: s.scope === "all" ? null : s.target })));
        }}
        aria-labelledby="scenarios-heading"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="scenarios-heading" className="text-lg font-semibold">
            {t("scenarios")}
          </h2>
          <p className="text-sm text-muted-foreground">{t("limit", { max: MAX_CHANGE, n: MAX_SCENARIOS })}</p>
        </div>
        <ol className="grid gap-3 lg:grid-cols-3">
          {scenarios.map((s, i) => (
            <li key={i} className="space-y-4 rounded-lg border bg-card p-4" data-testid="scenario">
              <div className="flex items-center justify-between">
                <h3 className="font-medium">{t("scenario", { n: i + 1 })}</h3>
                {scenarios.length > 1 ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setScenarios((list) => list.filter((_, j) => j !== i))}
                  >
                    <Trash2 aria-hidden="true" />
                    {t("remove")}
                    <span className="sr-only"> {t("scenario", { n: i + 1 })}</span>
                  </Button>
                ) : null}
              </div>
              <div className="space-y-1">
                <Label htmlFor={`lever-${i}`}>{t("lever")}</Label>
                <Select value={s.lever} onValueChange={(v) => update(i, { lever: v as Lever })}>
                  <SelectTrigger id={`lever-${i}`} className="w-full" data-testid={`lever-${i}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LEVERS.map((l) => (
                      <SelectItem key={l} value={l}>
                        {t(`levers.${l}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor={`scope-${i}`}>{t("scope")}</Label>
                  <Select
                    value={s.scope ?? "all"}
                    onValueChange={(v) => update(i, { scope: v as Scope, target: null })}
                  >
                    <SelectTrigger id={`scope-${i}`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SCOPES.map((sc) => (
                        <SelectItem key={sc} value={sc} disabled={sc !== "all" && !targets[sc].length}>
                          {t(`scopes.${sc}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {s.scope && s.scope !== "all" ? (
                  <div className="space-y-1">
                    <Label htmlFor={`target-${i}`}>{t(`target.${s.scope}`)}</Label>
                    <Select value={s.target ?? undefined} onValueChange={(v) => update(i, { target: v })}>
                      <SelectTrigger id={`target-${i}`} className="w-full" aria-invalid={!s.target}>
                        <SelectValue placeholder={t("choose")} />
                      </SelectTrigger>
                      <SelectContent>
                        {targets[s.scope].map((name) => (
                          <SelectItem key={name} value={name}>
                            <bdi>{name}</bdi>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor={`change-${i}`}>{t("change")}</Label>
                  <div className="flex items-center gap-1">
                    <Input
                      id={`change-${i}`}
                      type="number"
                      inputMode="numeric"
                      min={-MAX_CHANGE}
                      max={MAX_CHANGE}
                      step={1}
                      className="h-8 w-20 text-end tabular-nums"
                      value={s.change_pct}
                      onChange={(e) => update(i, { change_pct: clampChange(e.target.value) })}
                      data-testid={`change-${i}`}
                    />
                    <span aria-hidden="true">%</span>
                  </div>
                </div>
                <Slider
                  min={-MAX_CHANGE}
                  max={MAX_CHANGE}
                  step={1}
                  value={[s.change_pct]}
                  onValueChange={([v]) => update(i, { change_pct: clampChange(v ?? 0) })}
                  aria-label={t("changeSlider", { n: i + 1 })}
                  dir="ltr"
                />
                <div className="flex justify-between text-xs text-muted-foreground" dir="ltr" aria-hidden="true">
                  <span>−{MAX_CHANGE}%</span>
                  <span>0</span>
                  <span>+{MAX_CHANGE}%</span>
                </div>
              </div>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          {scenarios.length < MAX_SCENARIOS ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => setScenarios((list) => [...list, { ...START, lever: LEVERS[list.length % LEVERS.length] ?? "price" }])}
              data-testid="add-scenario"
            >
              <Plus aria-hidden="true" />
              {t("add")}
            </Button>
          ) : null}
          <Button type="submit" disabled={!ready || mutation.isPending} data-testid="run-whatif">
            {mutation.isPending ? t("running") : t("run")}
          </Button>
        </div>
        {!ready ? <p className="text-sm text-warn">{t("needTarget")}</p> : null}
      </form>

      {mutation.error ? <ErrorState title={t("failed")} error={mutation.error} /> : null}
      {mutation.data ? <Results results={mutation.data.scenarios} note={mutation.data.note} /> : null}
    </div>
  );
}

function Results({ results, note }: { results: ScenarioResult[]; note: string }) {
  const t = useTranslations("whatIf");
  const fmt = useFormat();
  const revenueDomain = sharedDomain(results.map((r) => r.revenue_change));
  const profitDomain = sharedDomain(results.map((r) => r.profit_change));
  const labels = { low: t("low"), expected: t("expected"), high: t("high") };
  return (
    <section aria-labelledby="results-heading" className="space-y-4" data-testid="whatif-results" aria-live="polite">
      <h2 id="results-heading" className="text-lg font-semibold">
        {t("results")}
      </h2>
      <ol className="grid gap-3 lg:grid-cols-3">
        {results.map((r, i) => {
          const warnings = [...(r.extrapolation_risk ? [t("outOfRange")] : []), ...r.warnings];
          return (
            <li key={i} className="space-y-4 rounded-lg border bg-card p-4" data-testid="scenario-result" data-status={r.status}>
              <div className="space-y-1">
                <h3 className="font-semibold">
                  {t("scenario", { n: i + 1 })}: {t(`levers.${r.lever}`)} {fmt.signedPct(r.change_pct)}
                </h3>
                <p className="text-sm text-muted-foreground">
                  {r.scope === "all" ? t("scopes.all") : <bdi>{r.target}</bdi>}
                </p>
              </div>
              {warnings.length ? (
                <div role="alert" className="flex gap-2 rounded-md border border-warn/50 bg-warn-soft p-3 text-sm" data-testid="whatif-warning">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
                  <ul className="space-y-1">
                    {warnings.map((w) => (
                      <li key={w}>
                        <EngineText>{w}</EngineText>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {r.status === "ok" && r.revenue_change && r.profit_change ? (
                <>
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium">{t("revenue")}</h4>
                    <RangeBar band={r.revenue_change} domain={revenueDomain} format={fmt.compact} labels={labels} />
                  </div>
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium">{t("profit")}</h4>
                    <RangeBar band={r.profit_change} domain={profitDomain} format={fmt.compact} labels={labels} />
                  </div>
                  <ConfidenceBadge level={r.confidence} sampleSize={r.sample_size} />
                </>
              ) : (
                <p className="rounded-md bg-muted p-3 text-sm" data-testid="not-enough-evidence">
                  {t("notEnough")}
                </p>
              )}
              <p className="text-sm">
                <EngineText>{r.summary}</EngineText>
              </p>
              {r.assumptions.length ? (
                <div className="space-y-1">
                  <h4 className="text-sm font-medium">{t("assumptions")}</h4>
                  <ul className="list-disc space-y-1 ps-5 text-sm text-muted-foreground" data-testid="assumptions">
                    {r.assumptions.map((a) => (
                      <li key={a}>
                        <EngineText>{a}</EngineText>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {r.evidence?.length ? (
                <details className="text-sm">
                  <summary className="cursor-pointer text-primary">{t("evidence")}</summary>
                  <div className="space-y-4 pt-3">
                    {r.evidence.map((q, k) => (
                      <EvidenceBlock key={k} query={q} />
                    ))}
                  </div>
                </details>
              ) : null}
            </li>
          );
        })}
      </ol>
      <p className="text-sm text-muted-foreground">
        <EngineText>{note}</EngineText>
      </p>
    </section>
  );
}
