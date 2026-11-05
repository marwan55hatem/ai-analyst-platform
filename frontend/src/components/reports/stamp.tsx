"use client";

import { useFormat } from "@/lib/format";

/**
 * A moment in time. Date and time are isolated apart: in Arabic, a single date-time string with
 * Latin digits can split across the line and swap its pieces in a narrow right-to-left row.
 */
export function Stamp({ iso, className }: { iso: string; className?: string }) {
  const fmt = useFormat();
  const [day, time] = fmt.dateTimeParts(iso);
  return (
    <time dateTime={iso} className={className}>
      <bdi>{day}</bdi> · <bdi>{time}</bdi>
    </time>
  );
}
