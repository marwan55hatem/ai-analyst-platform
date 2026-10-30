"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { Grain } from "@/lib/api/types";
import { grainAllowed, PRESETS, type DateRange, type Preset } from "@/lib/charts/range";

const GRAINS: Grain[] = ["day", "week", "month"];

/** Preset ranges, a custom from/to pair and the series grain, in one row above the charts. */
export function RangeControls({
  preset,
  range,
  data,
  grain,
  onPreset,
  onCustom,
  onGrain,
}: {
  preset: Preset | "custom";
  range: DateRange;
  data: DateRange;
  grain: Grain;
  onPreset: (p: Preset) => void;
  onCustom: (r: DateRange) => void;
  onGrain: (g: Grain) => void;
}) {
  const t = useTranslations("dashboard");
  const [draft, setDraft] = useState(range);
  const [custom, setCustom] = useState(preset === "custom");
  const valid = draft.start <= draft.end;
  return (
    <div className="space-y-3 rounded-lg border bg-card p-3" data-testid="range-controls">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div className="space-y-1">
          <span id="range-label" className="block text-xs text-muted-foreground">
            {t("range.label")}
          </span>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={custom ? "custom" : preset}
            onValueChange={(v) => {
              if (!v) return;
              if (v === "custom") {
                setDraft(range);
                setCustom(true);
                return;
              }
              setCustom(false);
              onPreset(v as Preset);
            }}
            aria-labelledby="range-label"
            className="flex-wrap"
          >
            {PRESETS.map((p) => (
              <ToggleGroupItem key={p} value={p} data-testid={`preset-${p}`}>
                {t(`range.${p}`)}
              </ToggleGroupItem>
            ))}
            <ToggleGroupItem value="custom" data-testid="preset-custom">
              {t("range.custom")}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
        <div className="space-y-1">
          <span id="grain-label" className="block text-xs text-muted-foreground">
            {t("grain.label")}
          </span>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={grain}
            onValueChange={(v) => v && onGrain(v as Grain)}
            aria-labelledby="grain-label"
          >
            {GRAINS.map((g) => (
              <ToggleGroupItem key={g} value={g} disabled={!grainAllowed(range, g)} data-testid={`grain-${g}`}>
                {t(`grain.${g}`)}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      </div>
      {custom ? (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) onCustom(draft);
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="range-start">{t("range.from")}</Label>
            <Input
              id="range-start"
              type="date"
              min={data.start}
              max={data.end}
              value={draft.start}
              onChange={(e) => setDraft((d) => ({ ...d, start: e.target.value }))}
              aria-invalid={!valid}
              aria-describedby={!valid ? "range-error" : undefined}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="range-end">{t("range.to")}</Label>
            <Input
              id="range-end"
              type="date"
              min={data.start}
              max={data.end}
              value={draft.end}
              onChange={(e) => setDraft((d) => ({ ...d, end: e.target.value }))}
              aria-invalid={!valid}
            />
          </div>
          <Button type="submit" disabled={!valid}>
            {t("range.apply")}
          </Button>
          {!valid ? (
            <p id="range-error" className="w-full text-sm text-bad">
              {t("range.invalid")}
            </p>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
