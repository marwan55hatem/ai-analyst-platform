"use client";

import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { errorCode, retryAfterSeconds } from "@/lib/errors";
import { MAX_UPLOAD_MB } from "@/lib/config";

/** Translated message for any error thrown by the API layer. */
export function useErrorMessage() {
  const t = useTranslations("errors");
  return (error: unknown): string => {
    const seconds = retryAfterSeconds(error);
    if (seconds !== null) {
      return seconds >= 90
        ? t("rateLimitedMinutes", { minutes: Math.ceil(seconds / 60) })
        : t("rateLimitedSeconds", { seconds });
    }
    const code = errorCode(error);
    if (code === "payload_too_large") return t("payload_too_large", { limit: MAX_UPLOAD_MB });
    return t(code);
  };
}

/** Inline error block with an optional retry. */
export function ErrorState({
  title,
  error,
  onRetry,
  children,
}: {
  title: ReactNode;
  error?: unknown;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  const t = useTranslations("common");
  const message = useErrorMessage();
  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-bad/40 bg-bad-soft p-4">
      <AlertTriangle className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden="true" />
      <div className="min-w-0 space-y-2">
        <p className="font-medium">{title}</p>
        {error !== undefined ? <p className="text-sm">{message(error)}</p> : null}
        {children}
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t("tryAgain")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
