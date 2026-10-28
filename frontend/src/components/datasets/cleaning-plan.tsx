"use client";

import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { Fix, FixChoice, Risk } from "@/lib/api/types";
import { useApplyPlan } from "@/lib/queries";

import { RiskBadge } from "./risk-badge";

export interface Choice {
  selected: boolean;
  strategy: string | null;
}

/**
 * Starting selection: only low-risk fixes the backend recommends are ticked. Medium and
 * high-risk fixes are never pre-selected, even when recommended. Null handling starts
 * at the backend default ("keep"); ambiguous dates start at the detected order.
 */
export function initialChoices(fixes: Fix[]): Record<string, Choice> {
  return Object.fromEntries(
    fixes.map((f) => [f.id, { selected: f.risk === "low" && f.recommended, strategy: f.default_strategy }]),
  );
}

/** Request body for the selected fixes. */
export function toApplyBody(fixes: Fix[], choices: Record<string, Choice>): FixChoice[] {
  return fixes
    .filter((f) => choices[f.id]?.selected)
    .map((f) => {
      const strategy = choices[f.id]?.strategy;
      return f.strategies && strategy ? { id: f.id, strategy } : { id: f.id };
    });
}

const RISK_ORDER: Risk[] = ["low", "medium", "high"];

function FixRow({
  fix,
  choice,
  onChange,
}: {
  fix: Fix;
  choice: Choice;
  onChange: (next: Choice) => void;
}) {
  const t = useTranslations("plan");
  const id = useId();
  const typeLabel = t.has(`fixTypes.${fix.type}`) ? t(`fixTypes.${fix.type}`) : fix.type;
  const isDates = fix.type === "parse_dates";

  return (
    <li className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 p-4" data-fix={fix.id} data-recommended={fix.recommended}>
      <Checkbox
        id={`${id}-check`}
        checked={choice.selected}
        onCheckedChange={(v) => onChange({ ...choice, selected: v === true })}
        aria-describedby={`${id}-desc`}
        className="mt-0.5"
      />
      <div className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Label htmlFor={`${id}-check`} className="font-medium">
            {typeLabel}
            {fix.columns.length ? (
              <span className="font-normal text-muted-foreground">
                {" · "}
                <bdi>{fix.columns.join(", ")}</bdi>
              </span>
            ) : null}
          </Label>
          <RiskBadge risk={fix.risk} />
          {fix.recommended ? (
            <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{t("recommended")}</span>
          ) : null}
        </div>
        <p id={`${id}-desc`} className="text-sm text-muted-foreground">
          <span lang="en" dir="ltr">
            {fix.description}
          </span>{" "}
          <span className="whitespace-nowrap">({t("affected", { rows: fix.rows_affected })})</span>
        </p>

        {fix.strategies && fix.strategies.length > 1 ? (
          <fieldset className="space-y-2 pt-1">
            <legend className="text-sm font-medium">{isDates ? t("dateOrder") : t("nullStrategy")}</legend>
            {isDates ? <p className="text-xs text-muted-foreground">{t("dateOrderHelp")}</p> : null}
            <RadioGroup
              value={choice.strategy ?? undefined}
              onValueChange={(strategy) => onChange({ ...choice, strategy })}
              className="gap-2"
            >
              {fix.strategies.map((s) => {
                const label = isDates
                  ? t.has(`dateOrders.${s}`) ? t(`dateOrders.${s}`) : s
                  : t.has(`strategies.${s}`) ? t(`strategies.${s}`) : s;
                const risk = fix.strategy_risk?.[s] as Risk | undefined;
                return (
                  <div key={s} className="flex flex-wrap items-center gap-2">
                    <RadioGroupItem value={s} id={`${id}-${s}`} />
                    <Label htmlFor={`${id}-${s}`} className="font-normal">
                      {label}
                    </Label>
                    {risk && !isDates ? <RiskBadge risk={risk} /> : null}
                  </div>
                );
              })}
            </RadioGroup>
          </fieldset>
        ) : null}
      </div>
    </li>
  );
}

/** Fixes grouped by risk, with an Apply button that creates a new version. */
export function CleaningPlan({
  datasetId,
  version,
  fixes,
}: {
  datasetId: string;
  version: number;
  fixes: Fix[];
}) {
  const t = useTranslations("plan");
  const errorMessage = useErrorMessage();
  const [choices, setChoices] = useState(() => initialChoices(fixes));
  const apply = useApplyPlan(datasetId, version);
  const body = useMemo(() => toApplyBody(fixes, choices), [fixes, choices]);

  if (!fixes.length) {
    return (
      <p className="flex items-center gap-2 text-good">
        <Sparkles className="size-5" aria-hidden="true" />
        {t("none")}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <p className="max-w-prose text-sm text-muted-foreground">{t("intro")}</p>
      {RISK_ORDER.map((risk) => {
        const group = fixes.filter((f) => f.risk === risk);
        if (!group.length) return null;
        return (
          <section key={risk} aria-labelledby={`risk-${risk}`} className="overflow-hidden rounded-lg border bg-card">
            <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b bg-muted/50 px-4 py-2.5">
              <h3 id={`risk-${risk}`} className="font-medium">
                {t(`risk.${risk}`)}
              </h3>
              <p className="text-xs text-muted-foreground">{t(`riskHelp.${risk}`)}</p>
            </header>
            <ul className="divide-y">
              {group.map((fix) => (
                <FixRow
                  key={fix.id}
                  fix={fix}
                  choice={choices[fix.id] ?? { selected: false, strategy: fix.default_strategy }}
                  onChange={(next) => setChoices((old) => ({ ...old, [fix.id]: next }))}
                />
              ))}
            </ul>
          </section>
        );
      })}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3">
        <p className="text-sm" aria-live="polite">
          {t("selectedCount", { count: body.length })}
        </p>
        <Button
          disabled={!body.length || apply.isPending}
          onClick={() =>
            apply.mutate(body, {
              onSuccess: (detail) =>
                toast.success(t("applied", { version: detail.latest_version?.version ?? version + 1 })),
              onError: (error) => toast.error(errorMessage(error)),
            })
          }
        >
          {apply.isPending ? t("applying") : t("apply")}
        </Button>
      </div>
    </div>
  );
}
