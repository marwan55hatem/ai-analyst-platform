"use client";

import { useLocale } from "next-intl";
import { useMemo } from "react";

import { intlLocale, toLocale } from "@/i18n/locales";

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** Locale-aware number, byte, date and relative-time formatters for the active UI locale. */
export function useFormat() {
  const locale = intlLocale(toLocale(useLocale()));
  return useMemo(() => {
    const integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const decimal = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    const percent = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 });
    const dateTime = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
    const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    return {
      int: (n: number) => integer.format(n),
      dec: (n: number) => decimal.format(n),
      pct: (fraction: number) => percent.format(fraction),
      bytes: (n: number) =>
        n >= 1024 * 1024
          ? `${decimal.format(n / (1024 * 1024))} MB`
          : `${decimal.format(Math.max(n, 1) / 1024)} KB`,
      dateTime: (iso: string) => dateTime.format(new Date(iso)),
      ago: (iso: string, now: number = Date.now()) => {
        const seconds = (new Date(iso).getTime() - now) / 1000;
        for (const [unit, size] of UNITS) {
          if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
        }
        return relative.format(0, "minute");
      },
    };
  }, [locale]);
}
