"use client";

import { useTranslations } from "next-intl";
import { useLayoutEffect, useRef, type ReactNode } from "react";

import type { ChatTurn } from "@/lib/chat/events";

import { Answer } from "./answer";
import { UserMessage } from "./user-message";

const NEAR_BOTTOM_PX = 80;

/** The conversation. It follows a new answer unless the reader has scrolled up to read. */
export function MessageList({
  turns,
  onRetry,
  onOpenInsight,
  empty,
}: {
  turns: ChatTurn[];
  onRetry: (turn: ChatTurn) => void;
  onOpenInsight: (id: string) => void;
  empty?: ReactNode;
}) {
  const t = useTranslations("chat");
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useLayoutEffect(() => {
    if (pinned.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  });

  return (
    <div
      ref={box}
      role="region"
      aria-label={t("thread")}
      tabIndex={0}
      data-testid="message-list"
      className="min-h-0 flex-1 overflow-y-auto rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onScroll={(event) => {
        const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
        pinned.current = scrollHeight - scrollTop - clientHeight < NEAR_BOTTOM_PX;
      }}
    >
      {turns.length ? (
        <ol className="space-y-8 pb-4">
          {turns.map((turn, index) => (
            <li key={turn.id} className="space-y-4" data-testid="turn">
              <UserMessage text={turn.question} />
              <Answer
                turn={turn}
                canRetry={index === turns.length - 1}
                onRetry={() => onRetry(turn)}
                onOpenInsight={onOpenInsight}
              />
            </li>
          ))}
        </ol>
      ) : (
        empty
      )}
    </div>
  );
}
