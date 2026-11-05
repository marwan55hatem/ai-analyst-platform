"use client";

import { AlertTriangle, BellOff, Check, CheckCheck, Info, Loader2, OctagonAlert } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { withDataset } from "@/lib/active-dataset";
import type { Alert, AlertLink, AlertSeverity } from "@/lib/api/types";
import { useAlerts, useMarkAllRead, useMarkRead } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { useAlertText } from "./alert-text";
import { Stamp } from "./stamp";

const SEVERITY_STYLE: Record<AlertSeverity, string> = {
  info: "bg-info-soft text-primary",
  warning: "bg-warn-soft text-warn",
  critical: "bg-bad-soft text-bad",
};
const SEVERITY_ICON = { info: Info, warning: AlertTriangle, critical: OctagonAlert };

/** Severity as an icon and a word, never colour alone. */
export function AlertSeverityBadge({ severity }: { severity: AlertSeverity }) {
  const t = useTranslations("alerts");
  const Icon = SEVERITY_ICON[severity];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        SEVERITY_STYLE[severity],
      )}
      data-severity={severity}
    >
      <Icon className="size-3.5" aria-hidden="true" />
      {t(`severity.${severity}`)}
    </span>
  );
}

/** Where an alert's "Open" link goes. */
export function alertHref(link: AlertLink | undefined, datasetId: string): string {
  const id = link?.dataset_id ?? datasetId;
  switch (link?.page) {
    case "churn":
      return withDataset("/insights/churn", id);
    case "insights":
      return withDataset("/insights", id);
    case "dataset":
      return `/datasets/${id}`;
    default:
      return withDataset("/dashboard", id);
  }
}

function AlertItem({ alert }: { alert: Alert }) {
  const t = useTranslations("alerts");
  const text = useAlertText()(alert);
  const markRead = useMarkRead();
  const message = useErrorMessage();
  const unread = alert.read_at === null;
  const page = alert.payload?.link?.page ?? "dashboard";
  const headingId = `alert-${alert.id}`;
  const textProps = text.engine ? { lang: "en", dir: "ltr" as const } : {};
  return (
    <li
      aria-labelledby={headingId}
      className={cn("rounded-lg border bg-card p-4", unread && "border-s-4 border-s-primary")}
      data-testid="alert-item"
      data-rule={alert.rule}
      data-unread={unread}
    >
      <div className="flex flex-wrap items-center gap-2">
        <AlertSeverityBadge severity={alert.severity} />
        {unread ? (
          <span className="text-xs font-semibold text-primary" data-testid="alert-unread">
            {t("unread")}
          </span>
        ) : null}
        <Stamp iso={alert.created_at} className="ms-auto text-xs text-muted-foreground" />
      </div>
      <h3 id={headingId} className="mt-2 font-semibold" {...textProps}>
        {text.title}
      </h3>
      <p className="mt-1 text-sm text-muted-foreground" {...textProps}>
        {text.body}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button asChild variant="outline" size="sm">
          <Link href={alertHref(alert.payload?.link, alert.dataset_id)}>{t(`open.${page}`)}</Link>
        </Button>
        {unread ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={markRead.isPending}
            aria-label={t("markReadName", { title: text.title })}
            onClick={() => markRead.mutate(alert.id, { onError: (e) => toast.error(message(e)) })}
          >
            <Check aria-hidden="true" />
            {t("markRead")}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

/** The alert feed: unread filter, mark all read and the alerts newest first. */
export function AlertsFeed() {
  const t = useTranslations("alerts");
  const message = useErrorMessage();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const feed = useAlerts(unreadOnly);
  const markAll = useMarkAllRead();
  const alerts = feed.data?.pages.flatMap((p) => p.items) ?? [];
  const anyUnread = alerts.some((a) => a.read_at === null);

  return (
    <section aria-labelledby="alerts-h" className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h2 id="alerts-h" className="text-lg font-semibold">
            {t("title")}
          </h2>
          <p className="max-w-prose text-sm text-muted-foreground">{t("intro")}</p>
          <Link href="/settings#alert-settings-h" className="text-sm text-primary underline underline-offset-4">
            {t("settingsLink")}
          </Link>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="alerts-unread-only"
              checked={unreadOnly}
              onCheckedChange={(v) => setUnreadOnly(v === true)}
            />
            <Label htmlFor="alerts-unread-only">{t("unreadOnly")}</Label>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={!anyUnread || markAll.isPending}
            onClick={() =>
              markAll.mutate(undefined, {
                onSuccess: () => toast.success(t("allRead")),
                onError: (e) => toast.error(message(e)),
              })
            }
          >
            <CheckCheck aria-hidden="true" />
            {t("markAllRead")}
          </Button>
        </div>
      </div>

      {feed.isPending ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : feed.error ? (
        <ErrorState title={t("loadFailed")} error={feed.error} onRetry={() => void feed.refetch()} />
      ) : alerts.length === 0 ? (
        <EmptyState
          icon={<BellOff />}
          title={unreadOnly ? t("empty.unreadTitle") : t("empty.title")}
          body={t("empty.body")}
        />
      ) : (
        <>
          <ul className="space-y-3" data-testid="alerts-feed">
            {alerts.map((a) => (
              <AlertItem key={a.id} alert={a} />
            ))}
          </ul>
          {feed.hasNextPage ? (
            <Button variant="outline" onClick={() => void feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
              {feed.isFetchingNextPage ? (
                <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : null}
              {t("loadMore")}
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}
