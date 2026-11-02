"use client";

import { Clock } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

/** The provider asked to wait. It counts down here; the next stream event removes it. */
export function StatusNotice({ until }: { until: number }) {
  const t = useTranslations("chat.status");
  const [now, setNow] = useState(() => Date.now());
  const [first] = useState(() => Math.ceil((until - Date.now()) / 1000));
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.ceil((until - now) / 1000);
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="status-notice">
      <Clock className="size-4 shrink-0" aria-hidden="true" />
      {/* Read out once; a screen reader should not hear every second. */}
      <span role="status" className="sr-only">
        {t("waiting", { seconds: first })}
      </span>
      <span aria-hidden="true">{seconds > 0 ? t("waiting", { seconds }) : t("retrying")}</span>
    </p>
  );
}
