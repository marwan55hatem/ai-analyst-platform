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

/** Parse an ISO date (yyyy-mm-dd) as midnight UTC. */
export function isoDay(iso: string): Date {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`);
}

/** Locale-aware number, byte, date and relative-time formatters for the active UI locale. */
export function useFormat() {
  const locale = intlLocale(toLocale(useLocale()));
  return useMemo(() => {
    const integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const decimal = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    const twoDecimals = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const percent = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 });
    const dateTime = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" });
    const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    const compact = new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 });
    const money = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const cents = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const signed = new Intl.NumberFormat(locale, { maximumFractionDigits: 1, signDisplay: "exceptZero" });
    const day = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" });
    const shortDay = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" });
    const month = new Intl.DateTimeFormat(locale, { month: "short", year: "numeric", timeZone: "UTC" });
    return {
      int: (n: number) => integer.format(n),
      dec: (n: number) => decimal.format(n),
      /** Scores where the second decimal matters (MASE, stability). */
      dec2: (n: number) => twoDecimals.format(n),
      pct: (fraction: number) => percent.format(fraction),
      bytes: (n: number) =>
        n >= 1024 * 1024
          ? `${decimal.format(n / (1024 * 1024))} MB`
          : `${decimal.format(Math.max(n, 1) / 1024)} KB`,
      dateTime: (iso: string) => dateTime.format(new Date(iso)),
      /** Short number for axes and tiles: 1.2M, 45K. */
      compact: (n: number) => compact.format(n),
      /** Money without a symbol (the file does not say which currency); cents below 100. */
      money: (n: number) => (Math.abs(n) < 100 ? cents.format(n) : money.format(n)),
      /** A percent value that is already x100, with its sign: +5.2%. */
      signedPct: (n: number) => `${signed.format(n)}%`,
      /** A signed plain number: +1.2. */
      signed: (n: number) => signed.format(n),
      /** A calendar date (ISO yyyy-mm-dd), never shifted by the viewer's time zone. */
      day: (iso: string) => day.format(isoDay(iso)),
      shortDay: (iso: string) => shortDay.format(isoDay(iso)),
      month: (iso: string) => month.format(isoDay(iso)),
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
