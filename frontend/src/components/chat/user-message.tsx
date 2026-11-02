"use client";

import { useTranslations } from "next-intl";

/** The question as it was typed. Its own direction decides how it reads, not the page's. */
export function UserMessage({ text }: { text: string }) {
  const t = useTranslations("chat");
  return (
    <div className="flex justify-end" data-testid="user-message">
      <span className="sr-only">{t("you")}</span>
      <p
        dir="auto"
        className="max-w-[85%] rounded-lg bg-muted px-4 py-2 text-start break-words whitespace-pre-wrap [overflow-wrap:anywhere]"
      >
        {text}
      </p>
    </div>
  );
}
