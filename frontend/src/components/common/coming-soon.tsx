"use client";

import { BarChart3, FileText, Lightbulb, MessagesSquare } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { EmptyState } from "./empty-state";
import { PageHeader } from "./page-header";

const ICONS = {
  dashboard: BarChart3,
  insights: Lightbulb,
  chat: MessagesSquare,
  reports: FileText,
};

/** Honest placeholder for sections built in later steps, pointing to what works today. */
export function ComingSoon({ section }: { section: keyof typeof ICONS }) {
  const t = useTranslations("placeholder");
  const Icon = ICONS[section];
  return (
    <>
      <PageHeader title={t(`${section}.title`)} />
      <EmptyState
        icon={<Icon />}
        title={t("notReady")}
        body={t(`${section}.body`)}
        action={
          <Button asChild>
            <Link href="/datasets">{t("cta")}</Link>
          </Button>
        }
      />
    </>
  );
}
