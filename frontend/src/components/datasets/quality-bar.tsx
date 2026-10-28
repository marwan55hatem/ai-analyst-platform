import { cn } from "@/lib/utils";

export type Band = "good" | "fair" | "poor";

/** Score bands used everywhere a quality score is shown. */
export function bandOf(score: number): Band {
  if (score >= 85) return "good";
  if (score >= 60) return "fair";
  return "poor";
}

const FILL: Record<Band, string> = { good: "bg-good", fair: "bg-warn", poor: "bg-bad" };

/** Compact score: the number (the information) plus a decorative bar. */
export function QualityBar({ score, label }: { score: number; label: string }) {
  const band = bandOf(score);
  return (
    <span className="inline-flex items-center gap-2">
      <span className="w-7 text-end font-medium tabular-nums">{score}</span>
      <span className="sr-only">{label}</span>
      <span aria-hidden="true" className="h-1.5 w-16 overflow-hidden rounded-full bg-dial-track">
        <span className={cn("block h-full rounded-full", FILL[band])} style={{ width: `${Math.max(2, score)}%` }} />
      </span>
    </span>
  );
}
