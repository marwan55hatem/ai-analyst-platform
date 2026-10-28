import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { DatasetList } from "@/components/datasets/dataset-list";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("datasets");
  return { title: t("title") };
}

export default function Page() {
  return <DatasetList />;
}
