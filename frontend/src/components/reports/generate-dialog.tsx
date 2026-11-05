"use client";

import { FilePlus2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { Dataset, ReportLocale } from "@/lib/api/types";
import { useCreateReport } from "@/lib/queries";

const INCLUDES = ["summary", "kpis", "trend", "contributors", "unusual", "customers", "caveats"] as const;

/** "Generate report": dataset and version from the active dataset, a language, what is inside. */
export function GenerateDialog({
  dataset,
  version,
  defaultLocale,
  disabled,
}: {
  dataset: Dataset;
  version: number;
  defaultLocale: ReportLocale;
  disabled?: boolean;
}) {
  const t = useTranslations("reports");
  const message = useErrorMessage();
  const create = useCreateReport();
  const [open, setOpen] = useState(false);
  const [locale, setLocale] = useState<ReportLocale>(defaultLocale);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { datasetId: dataset.id, version, locale },
      {
        onSuccess: () => {
          toast.success(t("queued"));
          setOpen(false);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) create.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button disabled={disabled} data-testid="report-generate">
          <FilePlus2 aria-hidden="true" />
          {t("generate")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t("generateTitle")}</DialogTitle>
            <DialogDescription>{t("generateBody")}</DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">{t("dataset")}</dt>
            <dd className="min-w-0 font-medium break-words" dir="auto">
              {dataset.name}
            </dd>
            <dt className="text-muted-foreground">{t("columns.version")}</dt>
            <dd>{t("version", { n: version })}</dd>
          </dl>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t("language")}</legend>
            <RadioGroup
              value={locale}
              onValueChange={(v) => setLocale(v as ReportLocale)}
              className="flex flex-wrap gap-4"
            >
              {(["en", "ar"] as const).map((l) => (
                <div key={l} className="flex items-center gap-2">
                  <RadioGroupItem id={`report-locale-${l}`} value={l} data-testid={`report-locale-${l}`} />
                  <Label htmlFor={`report-locale-${l}`}>{t(`languages.${l}`)}</Label>
                </div>
              ))}
            </RadioGroup>
          </fieldset>
          <div className="rounded-md bg-muted p-3 text-sm">
            <p className="font-medium">{t("includesTitle")}</p>
            <ul className="mt-1 list-disc space-y-0.5 ps-5">
              {INCLUDES.map((k) => (
                <li key={k}>{t(`includes.${k}`)}</li>
              ))}
            </ul>
            <p className="mt-2 text-muted-foreground">{t("excludes")}</p>
          </div>
          {create.error ? (
            <p role="alert" className="text-sm text-bad">
              {message(create.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={create.isPending} data-testid="report-submit">
              {create.isPending ? t("submitting") : t("submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
