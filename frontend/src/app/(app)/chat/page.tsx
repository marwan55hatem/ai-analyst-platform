import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { Suspense } from "react";

import { ComingSoon } from "@/components/common/coming-soon";
import { ChatInsightNote } from "@/components/insights/chat-insight-note";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("placeholder");
  return { title: t("chat.title") };
}

export default function Page() {
  return (
    <>
      <Suspense>
        <ChatInsightNote />
      </Suspense>
      <ComingSoon section="chat" />
    </>
  );
}
