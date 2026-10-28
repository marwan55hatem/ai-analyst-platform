"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Risk } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const STYLE: Record<Risk, string> = {
  low: "bg-good-soft text-good",
  medium: "bg-warn-soft text-warn",
  high: "bg-bad-soft text-bad",
};
const ICON = { low: ShieldCheck, medium: ShieldQuestion, high: ShieldAlert };

/** Risk shown with an icon and words (never colour alone). */
export function RiskBadge({ risk }: { risk: Risk }) {
  const t = useTranslations("plan");
  const Icon = ICON[risk];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLE[risk],
      )}
      data-risk={risk}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {t(`risk.${risk}`)}
    </span>
  );
}
