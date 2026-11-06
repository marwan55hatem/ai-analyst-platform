import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { ReportsView } from "@/components/reports/reports-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("reports");
  return { title: t("title") };
}

export default function Page() {
  return (
    <Suspense>
      <ReportsView />
    </Suspense>
  );
}
