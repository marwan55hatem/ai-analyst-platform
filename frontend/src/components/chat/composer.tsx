"use client";

import { SendHorizontal, Square } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/** Same limit as MAX_QUESTION_CHARS in backend/app/schemas/agent.py. */
export const MAX_QUESTION = 4_000;
const COUNT_FROM = MAX_QUESTION - 200;

/** The question box: Enter sends, Shift+Enter adds a line, and Stop replaces Send during an answer. */
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  waiting = false,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  /** The conversation is still loading, so a new question has no place in it yet. */
  waiting?: boolean;
}) {
  const t = useTranslations("chat.composer");
  const box = useRef<HTMLTextAreaElement>(null);
  const hintId = useId();
  const text = value.trim();
  const over = value.length - MAX_QUESTION;
  const canSend = Boolean(text) && over <= 0 && !streaming && !waiting;

  const send = () => {
    if (!canSend) return;
    onSend(text);
    box.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter also confirms a word in Arabic and other input methods; that must not send.
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    send();
  };

  return (
    <form
      className="space-y-1"
      data-testid="composer"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <div className="flex items-end gap-2">
        <Textarea
          ref={box}
          dir="auto"
          rows={1}
          value={value}
          aria-label={t("label")}
          aria-describedby={hintId}
          aria-invalid={over > 0 || undefined}
          placeholder={t("placeholder")}
          className="max-h-40 min-h-11 flex-1 resize-none bg-card text-start [field-sizing:content]"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
        />
        {streaming ? (
          <Button
            type="button"
            variant="outline"
            className="h-11"
            onClick={() => {
              onStop();
              box.current?.focus();
            }}
          >
            <Square className="fill-current" aria-hidden="true" />
            <span className="sr-only sm:not-sr-only">{t("stop")}</span>
          </Button>
        ) : (
          <Button type="submit" className="h-11" disabled={!canSend}>
            <SendHorizontal className="rtl:-scale-x-100" aria-hidden="true" />
            <span className="sr-only sm:not-sr-only">{t("send")}</span>
          </Button>
        )}
      </div>
      <p id={hintId} className="flex flex-wrap justify-between gap-x-4 text-xs text-muted-foreground">
        <span className="hidden sm:inline">{t("hint")}</span>
        {value.length >= COUNT_FROM ? (
          <span className={over > 0 ? "text-bad" : undefined} data-testid="composer-count">
            {over > 0 ? t("tooLong", { over }) : t("remaining", { count: MAX_QUESTION - value.length })}
          </span>
        ) : null}
      </p>
    </form>
  );
}
