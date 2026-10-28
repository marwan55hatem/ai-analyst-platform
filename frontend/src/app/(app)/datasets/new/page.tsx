import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { UploadForm } from "@/components/datasets/upload-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("upload");
  return { title: t("title") };
}

export default function Page() {
  return <UploadForm />;
}
