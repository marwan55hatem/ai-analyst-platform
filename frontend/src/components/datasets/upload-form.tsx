"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, UploadCloud, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useId, useRef, useState, type DragEvent } from "react";

import { useErrorMessage } from "@/components/common/error-state";
import { Field } from "@/components/common/field";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { checkFile, nameFromFile, uploadDataset } from "@/lib/api/upload";
import { MAX_UPLOAD_MB } from "@/lib/config";
import { useFormat } from "@/lib/format";
import { keys } from "@/lib/queries";
import { cn } from "@/lib/utils";

const ACCEPT = ".csv,.tsv,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export function UploadForm() {
  const t = useTranslations("upload");
  const fmt = useFormat();
  const router = useRouter();
  const client = useQueryClient();
  const errorMessage = useErrorMessage();
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [done, setDone] = useState(false);

  const busy = progress !== null;

  function pick(candidate: File | undefined) {
    if (!candidate) return;
    const issue = checkFile(candidate);
    if (issue) {
      setFile(null);
      setProblem(
        t(issue, { file: candidate.name, size: fmt.bytes(candidate.size), limit: MAX_UPLOAD_MB }),
      );
      return;
    }
    setProblem(null);
    setFile(candidate);
    if (!name.trim()) setName(nameFromFile(candidate.name));
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (!busy) pick(event.dataTransfer.files[0]);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) {
      inputRef.current?.click();
      return;
    }
    if (!name.trim()) {
      setNameError(t("nameRequired"));
      return;
    }
    setNameError(null);
    setProblem(null);
    setProgress(0);
    try {
      const detail = await uploadDataset({
        file,
        name: name.trim(),
        description: description.trim() || undefined,
        onProgress: setProgress,
      });
      setDone(true);
      client.setQueryData(keys.dataset(detail.id), detail);
      void client.invalidateQueries({ queryKey: keys.datasets });
      router.push(`/datasets/${detail.id}`);
    } catch (error) {
      setProgress(null);
      setProblem(errorMessage(error));
    }
  }

  return (
    <>
      <PageHeader title={t("title")} description={t("intro")} />
      <form onSubmit={onSubmit} noValidate className="max-w-2xl space-y-6">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed bg-card px-6 py-10 text-center transition-colors",
            dragging ? "border-primary bg-info-soft" : "border-input",
            problem && "border-bad",
          )}
        >
          <UploadCloud className="size-9 text-primary" aria-hidden="true" />
          <p className="text-lg font-medium">{t("dropTitle")}</p>
          <p id={hintId} className="max-w-md text-sm text-muted-foreground">
            {t("dropHint", { limit: MAX_UPLOAD_MB })}
          </p>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            data-testid="file-input"
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
          >
            {t("choose")}
          </Button>
        </div>

        {problem ? (
          <p role="alert" className="rounded-md bg-bad-soft px-4 py-3 text-sm font-medium text-bad">
            {problem}
          </p>
        ) : null}

        {file ? (
          <div className="flex items-center gap-3 rounded-lg border bg-card p-4">
            <FileSpreadsheet className="size-6 shrink-0 text-primary" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted-foreground">{t("selected")}</p>
              <p className="truncate font-medium" dir="auto">
                {file.name}
              </p>
              <p className="text-sm text-muted-foreground tabular-nums">{fmt.bytes(file.size)}</p>
            </div>
            {!busy ? (
              <Button type="button" variant="ghost" size="icon" aria-label={t("remove")} onClick={() => setFile(null)}>
                <X aria-hidden="true" />
              </Button>
            ) : null}
          </div>
        ) : null}

        <Field label={t("name")} hint={t("nameHint")} error={nameError}>
          <Input value={name} maxLength={200} dir="auto" disabled={busy} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t("description")}>
          <Textarea
            value={description}
            maxLength={2000}
            rows={3}
            dir="auto"
            disabled={busy}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>

        {busy ? (
          <div className="space-y-2" role="status" aria-live="polite">
            <Progress value={Math.round((progress ?? 0) * 100)} aria-label={t("title")} />
            <p className="text-sm text-muted-foreground">
              {done || (progress ?? 0) >= 1 ? t("uploaded") : t("uploading", { percent: fmt.pct(progress ?? 0) })}
            </p>
          </div>
        ) : null}

        <Button type="submit" disabled={busy}>
          {t("submit")}
        </Button>
      </form>
    </>
  );
}
