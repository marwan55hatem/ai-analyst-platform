import { APP_NAME } from "@/lib/config";
import { cn } from "@/lib/utils";

/** Product mark: a small calibrated dial (the data-health signature) and the name. */
export function Brand({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <svg viewBox="0 0 24 24" className="size-6 shrink-0" aria-hidden="true">
        <path
          d="M5.6 18.4A9 9 0 1 1 18.4 18.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="text-primary"
        />
        <path
          d="M12 12 16.6 7.4"
          stroke="var(--signal-fill)"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <circle cx="12" cy="12" r="1.8" className="fill-foreground" />
      </svg>
      <span>{APP_NAME}</span>
    </span>
  );
}
