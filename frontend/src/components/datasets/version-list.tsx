"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormat } from "@/lib/format";
import { useVersions } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Version history: number, date, source, rows, quality and the latest analysis of each version. */
export function VersionList({
  datasetId,
  selected,
  hrefFor,
}: {
  datasetId: string;
  /** The version currently shown on the page. */
  selected: number;
  hrefFor: (version: number) => string;
}) {
  const t = useTranslations("versions.list");
  const tc = useTranslations("common");
  const fmt = useFormat();
  const query = useVersions(datasetId);

  if (query.isPending) return <Skeleton className="h-32 w-full" />;
  if (query.isError)
    return (
      <ErrorState
        title={t("loadError")}
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  if (!query.data.length)
    return <p className="text-muted-foreground">{t("empty")}</p>;

  return (
    <div className="space-y-3">
      <p className="max-w-prose text-sm text-muted-foreground">{t("intro")}</p>
      <ul
        className="divide-y rounded-lg border bg-card"
        data-testid="version-list"
      >
        {query.data.map((v) => {
          const status = (v.analysis_status ??
            "none") as keyof typeof STATUS_TONE;
          return (
            <li
              key={v.id}
              data-testid="version-row"
              data-version={v.version}
              className={cn(
                "grid gap-x-6 gap-y-2 p-4 sm:grid-cols-[minmax(0,1.2fr)_repeat(4,minmax(0,1fr))]",
                v.version === selected && "bg-info-soft",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  href={hrefFor(v.version)}
                  className="font-semibold underline-offset-4 hover:underline"
                  aria-current={v.version === selected ? "true" : undefined}
                >
                  {tc("version", { version: v.version })}
                </Link>
                {v.is_current ? (
                  <Badge variant="secondary">{t("current")}</Badge>
                ) : null}
                {v.version === selected ? (
                  <Badge variant="outline">{t("viewing")}</Badge>
                ) : null}
              </div>
              <Cell label={t("date")}>
                <time dateTime={v.created_at}>{fmt.ago(v.created_at)}</time>
              </Cell>
              <Cell label={t("source")}>
                {t(
                  `sources.${v.source === "cleaning" ? "cleaning" : "upload"}`,
                )}
              </Cell>
              <Cell label={t("rows")}>
                {v.row_count === null ? "–" : fmt.int(v.row_count)}
              </Cell>
              <Cell label={t("quality")}>
                {v.quality_score === null ? "–" : v.quality_score}
              </Cell>
              <Cell
                label={t("analysis")}
                className="sm:col-start-2 sm:col-span-4"
              >
                <span className={STATUS_TONE[status] ?? ""}>
                  {t(`statuses.${status}`)}
                </span>
                {v.alerts_raised > 0 ? (
                  <span className="ms-2 text-muted-foreground">
                    · {t("alertsRaised", { count: v.alerts_raised })}
                  </span>
                ) : null}
              </Cell>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const STATUS_TONE = {
  none: "text-muted-foreground",
  pending: "text-primary",
  running: "text-primary",
  completed: "text-good",
  completed_with_warnings: "text-warn",
  failed: "text-bad",
};

function Cell({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <dl className={cn("min-w-0 text-sm", className)}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{children}</dd>
    </dl>
  );
}
