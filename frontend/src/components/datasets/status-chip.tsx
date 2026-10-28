"use client";

import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import type { DatasetVersion } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type ChipState = "processing" | "checking" | "ready" | "failed";

/** Collapse version + profile status into what the user cares about. */
export function chipState(v: DatasetVersion | null | undefined): ChipState {
  if (!v || v.status === "uploaded" || v.status === "processing") return "processing";
  if (v.status === "failed") return "failed";
  if (v.profile_status === "pending" || v.profile_status === "running") return "checking";
  return "ready";
}

const STYLE: Record<ChipState, string> = {
  processing: "bg-info-soft text-primary",
  checking: "bg-info-soft text-primary",
  ready: "bg-good-soft text-good",
  failed: "bg-bad-soft text-bad",
};

/** Status shown with an icon and words, never colour alone. */
export function StatusChip({ state }: { state: ChipState }) {
  const t = useTranslations("status");
  const Icon = state === "ready" ? CheckCircle2 : state === "failed" ? CircleAlert : Loader2;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLE[state],
      )}
    >
      <Icon
        className={cn("size-3.5", (state === "processing" || state === "checking") && "animate-spin motion-reduce:animate-none")}
        aria-hidden="true"
      />
      {t(state)}
    </span>
  );
}
