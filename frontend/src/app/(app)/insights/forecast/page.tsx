import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ForecastPage } from "@/components/insights/forecast-page";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("forecast");
  return { title: t("title") };
}

export default function Page() {
  return <ForecastPage />;
}
