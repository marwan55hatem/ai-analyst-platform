"use client";

import { FileSpreadsheet, UploadCloud, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import type { DatasetDetail } from "@/lib/api/types";
import { checkFile } from "@/lib/api/upload";
import { MAX_UPLOAD_MB } from "@/lib/config";
import { useFormat } from "@/lib/format";
import { useUploadVersion } from "@/lib/queries";
import { cn } from "@/lib/utils";

const ACCEPT =
  ".csv,.tsv,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/** "Upload new data": a complete new export of this dataset, analysed automatically by default. */
export function NewVersionDialog({
  datasetId,
  onUploaded,
  variant = "outline",
}: {
  datasetId: string;
  onUploaded: (detail: DatasetDetail, analyze: boolean) => void;
  variant?: "outline" | "default";
}) {
  const t = useTranslations("versions");
  const tu = useTranslations("upload");
  const fmt = useFormat();
  const errorMessage = useErrorMessage();
  const upload = useUploadVersion(datasetId);
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [analyze, setAnalyze] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);

  const busy = progress !== null;

  function reset() {
    setFile(null);
    setProblem(null);
    setProgress(null);
    setDragging(false);
    setAnalyze(true);
  }

  function pick(candidate: File | undefined) {
    if (!candidate) return;
    const issue = checkFile(candidate);
    if (issue) {
      setFile(null);
      setProblem(
        tu(issue, {
          file: candidate.name,
          size: fmt.bytes(candidate.size),
          limit: MAX_UPLOAD_MB,
        }),
      );
      return;
    }
    setProblem(null);
    setFile(candidate);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!busy) pick(event.dataTransfer.files[0]);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      inputRef.current?.click();
      return;
    }
    setProblem(null);
    setProgress(0);
    try {
      const detail = await upload.mutateAsync({
        file,
        analyze,
        onProgress: setProgress,
      });
      setOpen(false);
      reset();
      onUploaded(detail, analyze);
    } catch (error) {
      setProgress(null);
      setProblem(errorMessage(error));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant={variant} data-testid="new-version-open">
          <UploadCloud aria-hidden="true" />
          {t("uploadButton")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t("dialogTitle")}</DialogTitle>
            <DialogDescription>{t("dialogBody")}</DialogDescription>
          </DialogHeader>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed bg-card px-4 py-6 text-center transition-colors",
              dragging ? "border-primary bg-info-soft" : "border-input",
              problem && "border-bad",
            )}
          >
            <UploadCloud className="size-7 text-primary" aria-hidden="true" />
            <p className="font-medium">{tu("dropTitle")}</p>
            <p id={hintId} className="text-sm text-muted-foreground">
              {tu("dropHint", { limit: MAX_UPLOAD_MB })}
            </p>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              data-testid="new-version-file"
              onChange={(e) => {
                pick(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              aria-describedby={hintId}
              onClick={() => inputRef.current?.click()}
              data-testid="new-version-choose"
            >
              {tu("choose")}
            </Button>
          </div>
          <p className="text-sm font-medium" data-testid="complete-export-note">
            {t("completeExport")}
          </p>

          {problem ? (
            <p
              role="alert"
              className="rounded-md bg-bad-soft px-4 py-3 text-sm font-medium text-bad"
            >
              {problem}
            </p>
          ) : null}

          {file ? (
            <div className="flex items-center gap-3 rounded-lg border bg-card p-3">
              <FileSpreadsheet
                className="size-6 shrink-0 text-primary"
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="text-xs text-muted-foreground">
                  {tu("selected")}
                </p>
                <p
                  className="truncate font-medium"
                  dir="auto"
                  data-testid="new-version-name"
                >
                  {file.name}
                </p>
                <p className="text-sm text-muted-foreground tabular-nums">
                  {fmt.bytes(file.size)}
                </p>
              </div>
              {!busy ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={tu("remove")}
                  onClick={() => setFile(null)}
                >
                  <X aria-hidden="true" />
                </Button>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-start gap-3">
            <Checkbox
              id="new-version-analyze"
              checked={analyze}
              disabled={busy}
              onCheckedChange={(checked) => setAnalyze(checked === true)}
              aria-describedby="new-version-analyze-hint"
              data-testid="new-version-analyze"
              className="mt-1"
            />
            <div className="space-y-0.5">
              <Label htmlFor="new-version-analyze">{t("analyze")}</Label>
              <p
                id="new-version-analyze-hint"
                className="text-sm text-muted-foreground"
              >
                {t("analyzeHint")}
              </p>
            </div>
          </div>

          {busy ? (
            <div role="status" aria-live="polite" className="space-y-1">
              <p className="text-sm tabular-nums">
                {t("uploading", { percent: fmt.pct(progress ?? 0) })}
              </p>
              <Progress value={Math.round((progress ?? 0) * 100)} />
            </div>
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              {t("cancel")}
            </Button>
            <Button
              type="submit"
              disabled={busy || !file}
              data-testid="new-version-submit"
            >
              {t("submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
