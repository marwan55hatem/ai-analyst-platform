"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { AlertSettings, AlertSettingsIn } from "@/lib/api/types";
import { useAlertSettings, useSaveAlertSettings } from "@/lib/queries";

import { Row, SettingsCard } from "./settings-card";

/** The bounds the API enforces (backend/app/schemas/alerts.py). */
const FIELDS = [
  { key: "revenue_drop_pct", label: "revenue", unit: "percent", min: 1, max: 90, step: 0.5 },
  { key: "orders_drop_pct", label: "orders", unit: "percent", min: 1, max: 90, step: 0.5 },
  { key: "churn_increase_pct", label: "churn", unit: "percent", min: 1, max: 500, step: 1 },
  { key: "quality_drop_points", label: "quality", unit: "points", min: 1, max: 50, step: 1 },
] as const;
const DAYS = { min: 1, max: 365 };

type Draft = Record<(typeof FIELDS)[number]["key"] | "freshness_days", string> & { freshness_enabled: boolean };

function toDraft(s: AlertSettings): Draft {
  return {
    revenue_drop_pct: String(s.revenue_drop_pct),
    orders_drop_pct: String(s.orders_drop_pct),
    churn_increase_pct: String(s.churn_increase_pct),
    quality_drop_points: String(s.quality_drop_points),
    freshness_days: String(s.freshness_days),
    freshness_enabled: s.freshness_enabled,
  };
}

function EditForm({ settings }: { settings: AlertSettings }) {
  const t = useTranslations("settingsAlerts");
  const message = useErrorMessage();
  const save = useSaveAlertSettings();
  const [draft, setDraft] = useState<Draft>(() => toDraft(settings));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: AlertSettingsIn = {
      revenue_drop_pct: Number(draft.revenue_drop_pct),
      orders_drop_pct: Number(draft.orders_drop_pct),
      churn_increase_pct: Number(draft.churn_increase_pct),
      quality_drop_points: Math.round(Number(draft.quality_drop_points)),
      freshness_days: Math.round(Number(draft.freshness_days)),
      freshness_enabled: draft.freshness_enabled,
    };
    save.mutate(body, { onSuccess: () => toast.success(t("saved")) });
  };

  return (
    <form onSubmit={submit} className="mt-4 space-y-4" data-testid="alert-settings-form">
      <div className="grid gap-4 sm:grid-cols-2">
        {FIELDS.map((f) => (
          <div key={f.key} className="space-y-1.5">
            <Label htmlFor={`alert-${f.key}`}>
              {t(f.label)} ({f.unit === "percent" ? t("percentUnit") : t("pointsUnit")})
            </Label>
            <Input
              id={`alert-${f.key}`}
              type="number"
              inputMode="decimal"
              required
              min={f.min}
              max={f.max}
              step={f.step}
              value={draft[f.key]}
              aria-describedby={`alert-${f.key}-hint`}
              onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
            />
            <p id={`alert-${f.key}-hint`} className="text-xs text-muted-foreground">
              {t("bounds", { min: f.min, max: f.max })}
            </p>
          </div>
        ))}
      </div>
      <div className="space-y-3 rounded-md border p-3">
        <div className="flex items-center gap-2">
          <Checkbox
            id="alert-freshness"
            checked={draft.freshness_enabled}
            onCheckedChange={(v) => setDraft({ ...draft, freshness_enabled: v === true })}
          />
          <Label htmlFor="alert-freshness">{t("freshness")}</Label>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="alert-freshness-days">
            {t("freshnessDays")} ({t("daysUnit")})
          </Label>
          <Input
            id="alert-freshness-days"
            type="number"
            inputMode="numeric"
            required
            min={DAYS.min}
            max={DAYS.max}
            step={1}
            className="max-w-40"
            disabled={!draft.freshness_enabled}
            value={draft.freshness_days}
            aria-describedby="alert-freshness-days-hint"
            onChange={(e) => setDraft({ ...draft, freshness_days: e.target.value })}
          />
          <p id="alert-freshness-days-hint" className="text-xs text-muted-foreground">
            {t("bounds", { min: DAYS.min, max: DAYS.max })}
          </p>
        </div>
      </div>
      {save.error ? (
        <p role="alert" className="text-sm text-bad">
          {message(save.error)}
        </p>
      ) : null}
      <Button type="submit" disabled={save.isPending} data-testid="alert-settings-save">
        {save.isPending ? t("saving") : t("save")}
      </Button>
    </form>
  );
}

function ReadOnly({ settings }: { settings: AlertSettings }) {
  const t = useTranslations("settingsAlerts");
  return (
    <>
      <dl className="mt-2 divide-y" data-testid="alert-settings-readonly">
        {FIELDS.map((f) => (
          <Row key={f.key} label={t(f.label)}>
            {f.unit === "percent"
              ? t("percentValue", { n: settings[f.key] })
              : t("pointsValue", { n: settings[f.key] })}
          </Row>
        ))}
        <Row label={t("freshness")}>
          {settings.freshness_enabled ? t("on") : t("off")}
          {settings.freshness_enabled ? ` · ${t("daysValue", { n: settings.freshness_days })}` : ""}
        </Row>
      </dl>
      <p className="mt-2 text-sm text-muted-foreground">{t("readOnly")}</p>
    </>
  );
}

/** Settings → Alerts: owners and admins edit the thresholds, every other role reads them. */
export function AlertSettingsCard() {
  const t = useTranslations("settingsAlerts");
  const settings = useAlertSettings();
  return (
    <SettingsCard id="alert-settings-h" title={t("title")} className="xl:col-span-2">
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">{t("intro")}</p>
      {settings.isPending ? (
        <Skeleton className="mt-4 h-40 w-full" />
      ) : settings.error ? (
        <ErrorState title={t("loadFailed")} error={settings.error} onRetry={() => void settings.refetch()} />
      ) : (
        <>
          {settings.data.is_default ? (
            <p className="mt-2 text-sm text-muted-foreground">{t("defaults")}</p>
          ) : null}
          {settings.data.can_edit ? <EditForm settings={settings.data} /> : <ReadOnly settings={settings.data} />}
        </>
      )}
    </SettingsCard>
  );
}
