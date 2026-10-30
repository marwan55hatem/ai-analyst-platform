import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { WhatIfPage } from "@/components/insights/what-if-page";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("whatIf");
  return { title: t("title") };
}

export default function Page() {
  return <WhatIfPage />;
}
