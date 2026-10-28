"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export default function AppError({ reset }: { error: Error; reset: () => void }) {
  const t = useTranslations("errors");
  const tc = useTranslations("common");
  return (
    <div role="alert" className="max-w-prose space-y-4 rounded-lg border bg-card p-6">
      <h1 className="text-xl font-semibold">{t("title")}</h1>
      <p className="text-muted-foreground">{t("boundaryBody")}</p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={reset}>{tc("tryAgain")}</Button>
        <Button variant="outline" asChild>
          <Link href="/datasets">{tc("backToDatasets")}</Link>
        </Button>
      </div>
    </div>
  );
}
