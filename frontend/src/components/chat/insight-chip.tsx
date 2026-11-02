"use client";

import { Lightbulb, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { EngineText } from "@/components/insights/badges";
import { Button } from "@/components/ui/button";
import type { InsightContext } from "@/lib/chat/insight-context";

/** The finding a question is about, shown above the composer until it is sent or removed. */
export function InsightChip({ context, onRemove }: { context: InsightContext; onRemove: () => void }) {
  const t = useTranslations("chat.insight");
  if (context.kind === "missing" || context.kind === "older") {
    return (
      <p className="text-sm text-muted-foreground" data-testid="insight-note">
        {t(context.kind)}
      </p>
    );
  }
  if (context.kind !== "ready") return null;
  return (
    <div className="flex max-w-full items-center gap-2 rounded-md border bg-card py-1 ps-3 pe-1 text-sm" data-testid="insight-chip">
      <Lightbulb className="size-4 shrink-0 text-primary" aria-hidden="true" />
      <span className="shrink-0 text-muted-foreground">{t("label")}</span>
      <EngineText className="min-w-0 flex-1 truncate">{context.title}</EngineText>
      <Button type="button" variant="ghost" size="icon-sm" aria-label={t("remove")} onClick={onRemove}>
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}
