"use client";

import { useTranslations } from "next-intl";

import type { ChatTurn } from "@/lib/chat/events";

const SPOKEN_CHARS = 1_500;

/** Answer text for a screen reader: the marks of markdown are noise when read aloud. */
function spoken(markdown: string): string {
  return markdown
    .replace(/[*_#|`>~]|^-{3,}$/gm, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, SPOKEN_CHARS);
}

/**
 * The one live region of the chat. Its text changes when an answer starts and when it ends, never
 * per token, so a screen reader is not interrupted by the stream. Errors use their own alert.
 */
export function Announcer({ turn }: { turn: ChatTurn | null }) {
  const t = useTranslations("chat");
  let text = "";
  if (turn?.status === "streaming") text = t("announce.answering");
  else if (turn?.status === "done") text = `${t("announce.ready")} ${spoken(turn.answer)}`;
  else if (turn?.status === "stopped") text = t("stopped");
  return (
    <div className="sr-only" aria-live="polite" data-testid="announcer">
      {text}
    </div>
  );
}
