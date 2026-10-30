"use client";

import { Download, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { toast } from "sonner";

import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { downloadCsv } from "@/lib/download";

/** "Export CSV" for a server table: the same filters and sort as on screen. */
export function ExportButton({
  path,
  params,
  fallback,
}: {
  path: string;
  params: Record<string, string | number | undefined | null>;
  fallback: string;
}) {
  const t = useTranslations("table");
  const message = useErrorMessage();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      data-testid="export-csv"
      onClick={async () => {
        setBusy(true);
        try {
          await downloadCsv(path, params, fallback);
          toast.success(t("exported"));
        } catch (error) {
          toast.error(message(error));
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Download aria-hidden="true" />}
      {t("export")}
    </Button>
  );
}
