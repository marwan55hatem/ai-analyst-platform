"use client";

import { FileSpreadsheet, Upload } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Dataset } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { useDatasets, useLastRun } from "@/lib/queries";

import { QualityBar } from "./quality-bar";
import { StatusChip, chipState } from "./status-chip";

function LastAnalysis({ dataset }: { dataset: Dataset }) {
  const t = useTranslations("datasets");
  const fmt = useFormat();
  const { data, isPending } = useLastRun(dataset.id);
  if (isPending) return <Skeleton className="h-4 w-20" />;
  const at = data?.finished_at ?? data?.started_at;
  if (!at) return <span className="text-muted-foreground">{t("notAnalysed")}</span>;
  return <time dateTime={at}>{fmt.ago(at)}</time>;
}

function Quality({ dataset }: { dataset: Dataset }) {
  const t = useTranslations("datasets");
  const score = dataset.latest_version?.quality_score;
  if (score === null || score === undefined) {
    return <span className="text-muted-foreground">{t("noScore")}</span>;
  }
  return <QualityBar score={score} label={t("qualityOf", { score })} />;
}

function Rows({ dataset }: { dataset: Dataset }) {
  const fmt = useFormat();
  const rows = dataset.latest_version?.row_count;
  return <span className="tabular-nums">{rows === null || rows === undefined ? "–" : fmt.int(rows)}</span>;
}

function ListSkeleton() {
  return (
    <div className="space-y-2" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  );
}

export function DatasetList() {
  const t = useTranslations("datasets");
  const fmt = useFormat();
  const { data, isPending, isError, error, refetch } = useDatasets();

  const uploadButton = (
    <Button asChild>
      <Link href="/datasets/new">
        <Upload aria-hidden="true" />
        {t("upload")}
      </Link>
    </Button>
  );

  return (
    <>
      <PageHeader title={t("title")} description={t("intro")} actions={data?.length ? uploadButton : null} />
      {isPending ? (
        <ListSkeleton />
      ) : isError ? (
        <ErrorState title={t("loadError")} error={error} onRetry={() => void refetch()} />
      ) : data.length === 0 ? (
        <EmptyState icon={<FileSpreadsheet />} title={t("emptyTitle")} body={t("emptyBody")} action={uploadButton} />
      ) : (
        <>
          {/* Phones: one card per dataset. */}
          <ul className="space-y-3 md:hidden">
            {data.map((d) => (
              <li key={d.id}>
                <Link
                  href={`/datasets/${d.id}`}
                  className="block space-y-3 rounded-lg border bg-card p-4 hover:border-primary/60"
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 font-medium break-words" dir="auto">
                      {d.name}
                    </span>
                    <StatusChip state={chipState(d.latest_version)} />
                  </div>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                    <div>
                      <dt className="text-muted-foreground">{t("colQuality")}</dt>
                      <dd>
                        <Quality dataset={d} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">{t("colRows")}</dt>
                      <dd>
                        <Rows dataset={d} />
                      </dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">{t("colAnalysis")}</dt>
                      <dd>
                        <LastAnalysis dataset={d} />
                      </dd>
                    </div>
                  </dl>
                </Link>
              </li>
            ))}
          </ul>

          {/* Wider screens: a table. */}
          <div className="hidden overflow-hidden rounded-lg border bg-card md:block">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/60 hover:bg-muted/60">
                  <TableHead className="ps-4">{t("colName")}</TableHead>
                  <TableHead>{t("colStatus")}</TableHead>
                  <TableHead className="text-end">{t("colRows")}</TableHead>
                  <TableHead>{t("colQuality")}</TableHead>
                  <TableHead>{t("colAnalysis")}</TableHead>
                  <TableHead className="pe-4">{t("colUpdated")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="max-w-[18rem] ps-4">
                      <Link
                        href={`/datasets/${d.id}`}
                        className="block truncate font-medium text-primary underline-offset-4 hover:underline"
                        dir="auto"
                      >
                        {d.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusChip state={chipState(d.latest_version)} />
                    </TableCell>
                    <TableCell className="text-end">
                      <Rows dataset={d} />
                    </TableCell>
                    <TableCell>
                      <Quality dataset={d} />
                    </TableCell>
                    <TableCell>
                      <LastAnalysis dataset={d} />
                    </TableCell>
                    <TableCell className="pe-4 text-muted-foreground">
                      <time dateTime={d.created_at}>{fmt.ago(d.created_at)}</time>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </>
  );
}
