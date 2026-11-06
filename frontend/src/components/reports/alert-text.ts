"use client";

import { useTranslations } from "next-intl";

import type { Alert, AlertPayload } from "@/lib/api/types";
import { useFormat } from "@/lib/format";

type Fmt = ReturnType<typeof useFormat>;

/** Money as the analysis states it: a currency code only when the company profile gives one. */
function money(fmt: Fmt, value: number | undefined, currency?: string | null): string {
  if (value === undefined || value === null) return "–";
  const n = fmt.money(value);
  return currency ? `${currency} ${n}` : n;
}

function has(p: AlertPayload, ...keys: (keyof AlertPayload)[]): boolean {
  return keys.every((k) => p[k] !== undefined && p[k] !== null);
}

/**
 * The alert in the reader's language, built from the numbers in its payload. When a payload
 * lacks what a sentence needs, the stored English text is used instead (`engine: true`).
 */
export function useAlertText() {
  const t = useTranslations("alerts");
  const fmt = useFormat();

  return (alert: Alert): { title: string; body: string; engine: boolean } => {
    const p = alert.payload ?? {};
    const fallback = { title: alert.title, body: alert.body, engine: true };
    const c = p.currency;
    switch (alert.rule) {
      case "revenue_drop":
      case "orders_drop": {
        if (p.method === "forecast" && has(p, "period", "current", "range_low", "range_high", "expected")) {
          const month = fmt.month(p.period!);
          return {
            title: t("forecastTitle", { month }),
            body: t("forecastBody", {
              month,
              actual: money(fmt, p.current, c),
              level: p.range_level ?? 80,
              low: money(fmt, p.range_low, c),
              high: money(fmt, p.range_high, c),
              expected: money(fmt, p.expected, c),
              through: p.forecast_made_through ? fmt.month(p.forecast_made_through) : "–",
            }),
            engine: false,
          };
        }
        if (!has(p, "period", "previous_period", "current", "previous", "change_pct")) return fallback;
        const metric = t(`metric.${p.metric === "orders" ? "orders" : "net_revenue"}`);
        const show = (v: number | undefined) => (p.metric === "orders" ? fmt.int(v ?? 0) : money(fmt, v, c));
        const pct = `${fmt.dec(Math.abs(p.change_pct!))}%`;
        const month = fmt.month(p.period!);
        const previous = fmt.month(p.previous_period!);
        return {
          title: t("momTitle", { metric, month, pct, previous }),
          body: t("momBody", { metric, month, actual: show(p.current), pct, previous, before: show(p.previous) }),
          engine: false,
        };
      }
      case "new_unusual_days": {
        if (!has(p, "start", "current", "expected", "previous_last_date")) return fallback;
        const when =
          p.end && p.end !== p.start ? t("range", { from: fmt.day(p.start!), to: fmt.day(p.end) }) : fmt.day(p.start!);
        return {
          title: t("dayTitle", { kind: p.kind === "drop" ? "drop" : "spike", when }),
          body: t("dayBody", {
            actual: money(fmt, p.current, c),
            expected: money(fmt, p.expected, c),
            last: fmt.day(p.previous_last_date!),
          }),
          engine: false,
        };
      }
      case "churn_up": {
        if (!has(p, "previous_customers", "current_customers", "previous_version", "current_version")) return fallback;
        return {
          title: t("churnTitle"),
          body: t("churnBody", {
            band: p.band === "high" ? "high" : "top",
            before: fmt.int(p.previous_customers!),
            after: fmt.int(p.current_customers!),
            va: p.previous_version!,
            vb: p.current_version!,
            spendBefore: money(fmt, p.previous_yearly_spend, c),
            spendAfter: money(fmt, p.current_yearly_spend, c),
          }),
          engine: false,
        };
      }
      case "new_dead_stock": {
        if (!has(p, "count", "names")) return fallback;
        return {
          title: t("deadTitle", { count: p.count! }),
          body: t("deadBody", {
            days: p.days ?? "–",
            asOf: p.as_of ? fmt.day(p.as_of) : "–",
            names: p.names!.join(", "),
          }),
          engine: false,
        };
      }
      case "quality_down": {
        if (!has(p, "previous", "current", "change_points", "previous_version", "current_version")) return fallback;
        return {
          title: t("qualityTitle", { points: Math.abs(p.change_points!), version: p.current_version! }),
          body: t("qualityBody", {
            before: p.previous!,
            after: p.current!,
            va: p.previous_version!,
            vb: p.current_version!,
          }),
          engine: false,
        };
      }
      case "stale_data": {
        if (!has(p, "dataset_name", "days", "last_date", "threshold_days")) return fallback;
        return {
          title: t("staleTitle", { name: p.dataset_name!, days: p.days! }),
          body: t("staleBody", { last: fmt.day(p.last_date!), threshold: p.threshold_days! }),
          engine: false,
        };
      }
      default:
        return fallback;
    }
  };
}
