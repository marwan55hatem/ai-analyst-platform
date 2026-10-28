import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { SettingsView } from "@/components/settings/settings-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("settings");
  return { title: t("title") };
}

export default function Page() {
  return <SettingsView />;
}
