import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ComingSoon } from "@/components/common/coming-soon";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("placeholder");
  return { title: t("dashboard.title") };
}

export default function Page() {
  return <ComingSoon section="dashboard" />;
}
