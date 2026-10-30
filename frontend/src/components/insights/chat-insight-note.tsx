"use client";

import { MessagesSquare } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { useInsight } from "@/lib/queries";

import { EngineText } from "./badges";

/**
 * On the chat placeholder: the insight the user wanted to ask about (?insight=ID), so the hand-off
 * from the evidence drawer is visible until the chat arrives in Step 13.
 */
export function ChatInsightNote() {
  const t = useTranslations("insights");
  const id = useSearchParams().get("insight");
  const found = useInsight(id);
  if (!id) return null;
  return (
    <div className="mb-6 flex gap-3 rounded-lg border bg-card p-4" data-testid="chat-insight">
      <MessagesSquare className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <p className="font-medium">{t("chatContext")}</p>
        <p className="text-sm text-muted-foreground">
          {found.data ? <EngineText>{found.data.title}</EngineText> : t("drawer.loading")}
        </p>
      </div>
    </div>
  );
}
