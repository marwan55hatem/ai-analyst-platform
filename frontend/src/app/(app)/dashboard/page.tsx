import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { DashboardPage } from "@/components/dashboard/dashboard-page";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("dashboard");
  return { title: t("title") };
}

export default function Page() {
  return (
    <Suspense>
      <DashboardPage />
    </Suspense>
  );
}
