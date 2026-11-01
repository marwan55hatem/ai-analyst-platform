"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { ChatErrorInfo } from "@/lib/chat/events";
import { chatErrorView } from "@/lib/chat/errors";

const MINUTES_FROM_S = 120;

/** What went wrong with an answer in plain words, and the one thing to do next. */
export function ChatError({ error, onRetry }: { error: ChatErrorInfo; onRetry: () => void }) {
  const t = useTranslations("chat.errors");
  const tc = useTranslations("chat");
  const view = chatErrorView(error.code, error.retryAfterS);
  const wait = view.action === "wait" ? (view.seconds ?? 0) : 0;
  const [ready, setReady] = useState(wait === 0);
  useEffect(() => {
    if (wait === 0) return;
    const id = setTimeout(() => setReady(true), wait * 1000);
    return () => clearTimeout(id);
  }, [wait]);

  const long = wait >= MINUTES_FROM_S;
  const key = long ? `${view.key}Minutes` : view.key;
  const message = t(key as "generic", { seconds: wait, minutes: Math.ceil(wait / 60) });
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-md border border-bad/40 bg-bad-soft p-3 text-sm"
      data-testid="chat-error"
      data-code={error.code}
    >
      <p className="flex min-w-0 flex-1 basis-60 items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden="true" />
        <span>{message}</span>
      </p>
      {view.action === "retry" || view.action === "wait" ? (
        <Button type="button" variant="outline" size="sm" disabled={!ready} onClick={onRetry}>
          <RotateCcw aria-hidden="true" />
          {tc("retry")}
        </Button>
      ) : null}
      {view.action === "settings" ? (
        <Button asChild variant="outline" size="sm">
          <Link href="/settings">{t("seeUsage")}</Link>
        </Button>
      ) : null}
    </div>
  );
}
