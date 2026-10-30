import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { InsightsFeed } from "@/components/insights/insights-feed";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("insights");
  return { title: t("title") };
}

export default function Page() {
  return <InsightsFeed />;
}
