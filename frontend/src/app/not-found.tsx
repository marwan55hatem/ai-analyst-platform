import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { Brand } from "@/components/common/brand";
import { Button } from "@/components/ui/button";

export default async function NotFound() {
  const t = await getTranslations("errors");
  const tc = await getTranslations("common");
  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-prose flex-col justify-center gap-4 px-6">
      <Brand className="text-lg" />
      <h1 className="text-2xl font-semibold">{t("notFoundTitle")}</h1>
      <p className="text-muted-foreground">{t("notFoundBody")}</p>
      <div>
        <Button asChild>
          <Link href="/datasets">{tc("backToDatasets")}</Link>
        </Button>
      </div>
    </main>
  );
}
