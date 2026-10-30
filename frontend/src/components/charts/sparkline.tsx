import { memo } from "react";

/** Points of a sparkline in a 100 x 32 box (higher values nearer the top). */
export function sparkPath(values: readonly number[], width = 100, height = 32, pad = 3): string {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  return values
    .map((v, i) => {
      const x = i * step;
      const y = pad + (1 - (v - min) / span) * (height - 2 * pad);
      return `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

/**
 * A small trend line next to a KPI. It is decorative: the card states the value and change in
 * words, so the line is hidden from screen readers. Time runs left to right in both languages.
 */
export const Sparkline = memo(function Sparkline({ values, className }: { values: readonly number[]; className?: string }) {
  const d = sparkPath(values);
  if (!d) return null;
  return (
    <svg
      viewBox="0 0 100 32"
      preserveAspectRatio="none"
      className={className}
      aria-hidden="true"
      data-testid="sparkline"
    >
      <path d={d} fill="none" stroke="var(--chart-actual)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
});
