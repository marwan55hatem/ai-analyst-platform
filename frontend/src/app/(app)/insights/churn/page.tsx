import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ChurnPage } from "@/components/insights/churn-page";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("churn");
  return { title: t("title") };
}

export default function Page() {
  return <ChurnPage />;
}
