"use client";

import { CheckCircle2, Clock, Download, FileText, Loader2, Trash2, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { isReportActive, type Report, type ReportStatus } from "@/lib/api/types";
import { downloadCsv } from "@/lib/download";
import { useFormat } from "@/lib/format";
import { useDeleteReport, useReports } from "@/lib/queries";
import { cn } from "@/lib/utils";

import { Stamp } from "./stamp";

const STATUS_STYLE: Record<ReportStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-info-soft text-primary",
  completed: "bg-good-soft text-good",
  failed: "bg-bad-soft text-bad",
};
const STATUS_ICON = { queued: Clock, running: Loader2, completed: CheckCircle2, failed: XCircle };
const FAILED_CODES = ["no_analysis", "too_large", "render_failed", "timed_out", "not_started"];

function StatusChip({ status }: { status: ReportStatus }) {
  const t = useTranslations("reports");
  const Icon = STATUS_ICON[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STATUS_STYLE[status],
      )}
      data-status={status}
    >
      <Icon
        className={cn("size-3.5", status === "running" && "animate-spin motion-reduce:animate-none")}
        aria-hidden="true"
      />
      {t(`status.${status}`)}
    </span>
  );
}

function DownloadButton({ report }: { report: Report }) {
  const t = useTranslations("reports");
  const message = useErrorMessage();
  const [busy, setBusy] = useState(false);
  const language = t(`languages.${report.locale}`);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      data-testid="report-download"
      aria-label={t("downloadName", { language, name: report.title, version: report.version })}
      onClick={async () => {
        setBusy(true);
        try {
          await downloadCsv(`/api/v1/reports/${report.id}/download`, {}, `report-${report.version}.pdf`);
          toast.success(t("downloaded"));
        } catch (error) {
          toast.error(message(error));
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? (
        <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <Download aria-hidden="true" />
      )}
      {t("download")}
    </Button>
  );
}

function DeleteDialog({ report, onClose }: { report: Report | null; onClose: () => void }) {
  const t = useTranslations("reports");
  const message = useErrorMessage();
  const remove = useDeleteReport();
  const language = report ? t(`languages.${report.locale}`) : "";
  return (
    <Dialog open={report !== null} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {report ? t("deleteBody", { language, name: report.title, version: report.version }) : null}
          </DialogDescription>
        </DialogHeader>
        {remove.error ? (
          <p role="alert" className="text-sm text-bad">
            {message(remove.error)}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            data-testid="report-delete-confirm"
            onClick={() =>
              report &&
              remove.mutate(report.id, {
                onSuccess: () => {
                  toast.success(t("deleted"));
                  onClose();
                },
              })
            }
          >
            {remove.isPending ? t("deleting") : t("deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ReportRow({ report, onDelete }: { report: Report; onDelete: (r: Report) => void }) {
  const t = useTranslations("reports");
  const fmt = useFormat();
  const language = t(`languages.${report.locale}`);
  const failedKey = FAILED_CODES.includes(report.error_code ?? "") ? report.error_code! : "generic";
  return (
    <li className="rounded-lg border bg-card p-4" data-testid="report-row" data-status={report.status}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="font-semibold break-words" dir="auto">
            {report.title}
          </p>
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <div className="flex gap-1">
              <dt className="sr-only">{t("columns.version")}</dt>
              <dd>{t("version", { n: report.version })}</dd>
            </div>
            <div className="flex gap-1">
              <dt className="sr-only">{t("columns.language")}</dt>
              <dd>{language}</dd>
            </div>
            {report.size_bytes ? (
              <div className="flex gap-1">
                <dt className="sr-only">{t("columns.size")}</dt>
                <dd>{fmt.bytes(report.size_bytes)}</dd>
              </div>
            ) : null}
            <div className="flex gap-1">
              <dt className="sr-only">{t("columns.created")}</dt>
              <dd>
                <Stamp iso={report.created_at} />
              </dd>
            </div>
          </dl>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <StatusChip status={report.status} />
          {report.status === "completed" ? <DownloadButton report={report} /> : null}
          {!isReportActive(report.status) ? (
            <Button
              variant="ghost"
              size="sm"
              data-testid="report-delete"
              aria-label={t("deleteName", { language, name: report.title, version: report.version })}
              onClick={() => onDelete(report)}
            >
              <Trash2 aria-hidden="true" />
              {t("delete")}
            </Button>
          ) : null}
        </div>
      </div>
      {isReportActive(report.status) ? (
        <div className="mt-3 space-y-1">
          <Progress value={report.progress} aria-label={t("progress", { pct: report.progress })} />
          <p className="text-xs text-muted-foreground">{t("progress", { pct: report.progress })}</p>
        </div>
      ) : null}
      {report.status === "failed" ? (
        <p className="mt-3 text-sm text-bad" data-testid="report-failed">
          {t(`failedReason.${failedKey}`)}
        </p>
      ) : null}
    </li>
  );
}

/** The company's reports with status, download and delete; polls while one is generating. */
export function ReportList() {
  const t = useTranslations("reports");
  const reports = useReports();
  const [deleting, setDeleting] = useState<Report | null>(null);
  if (reports.isPending) {
    return (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (reports.error) {
    return <ErrorState title={t("loadFailed")} error={reports.error} onRetry={() => void reports.refetch()} />;
  }
  const items = reports.data.items;
  if (items.length === 0) {
    return <EmptyState icon={<FileText />} title={t("empty.title")} body={t("empty.body")} />;
  }
  const active = items.find((r) => isReportActive(r.status));
  return (
    <>
      <p className="sr-only" aria-live="polite">
        {active ? t(`status.${active.status}`) : ""}
      </p>
      <ul className="space-y-3" data-testid="report-list">
        {items.map((r) => (
          <ReportRow key={r.id} report={r} onDelete={setDeleting} />
        ))}
      </ul>
      <DeleteDialog report={deleting} onClose={() => setDeleting(null)} />
    </>
  );
}
