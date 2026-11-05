"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useFormat } from "@/lib/format";
import { useUnreadCount } from "@/lib/queries";

/**
 * Header bell with the unread count. Its name carries the count; a polite live region speaks
 * only when the count rises (new alerts), not on every poll or on the first load.
 */
export function AlertsBell() {
  const t = useTranslations("alerts");
  const fmt = useFormat();
  const unread = useUnreadCount();
  const count = unread.data ?? 0;
  const seen = useRef<number | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (unread.data === undefined) return;
    if (seen.current !== null && unread.data > seen.current) {
      setAnnouncement(t("announce", { count: unread.data - seen.current }));
    }
    seen.current = unread.data;
  }, [unread.data, t]);

  return (
    <>
      <Button asChild variant="ghost" size="icon" className="relative">
        <Link href="/reports?tab=alerts" aria-label={t("bell", { count })} data-testid="alerts-bell">
          <Bell aria-hidden="true" />
          {count > 0 ? (
            <span
              aria-hidden="true"
              data-testid="alerts-bell-count"
              className="absolute -end-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-bad px-1 text-[0.625rem] leading-none font-semibold text-background"
            >
              {count > 99 ? "99+" : fmt.int(count)}
            </span>
          ) : null}
        </Link>
      </Button>
      <span className="sr-only" aria-live="polite" data-testid="alerts-announcer">
        {announcement}
      </span>
    </>
  );
}
