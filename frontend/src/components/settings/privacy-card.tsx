"use client";

import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";
import { useAiUsage, useCompanyProfile } from "@/lib/queries";

import { providerName } from "./ai-usage-card";
import { SettingsCard } from "./settings-card";

// The same list as docs/agent-design.md §7 and the "What reaches Groq" row of docs/known-limits.md.
const SENT = ["instructions", "profile", "question", "columns", "results", "customerIds", "samples"] as const;

/** The company's personal-data policy and what reaches the model. Read-only: there is no toggle. */
export function PrivacyCard() {
  const t = useTranslations("settings.ai");
  const profile = useCompanyProfile();
  const usage = useAiUsage();
  const provider = usage.data?.provider;
  return (
    <SettingsCard id="privacy-h" title={t("privacyTitle")} className="xl:col-span-2">
      <div className="mt-3 max-w-prose space-y-3" data-testid="privacy">
        {profile.isPending ? (
          <Skeleton className="h-12 w-full" />
        ) : (
          <p className="font-medium">{t(profile.data?.allow_pii ? "piiOn" : "piiOff")}</p>
        )}
        <p className="text-sm text-muted-foreground">{t("piiChange")}</p>
        <div className="space-y-1">
          <p>{t("sentIntro")}</p>
          <ul className="list-disc space-y-1 ps-5 text-sm">
            {SENT.map((key) => (
              <li key={key}>{t(`sent.${key}`)}</li>
            ))}
          </ul>
        </div>
        <p className="text-sm">{t("notTotals")}</p>
        {provider === "groq" ? <p className="text-sm">{t("groq")}</p> : null}
        {provider === "fake" ? <p className="text-sm">{t("fakeLocal")}</p> : null}
        {provider && provider !== "groq" && provider !== "fake" && provider !== "none" ? (
          <p className="text-sm">{t("otherCloud", { provider: providerName(provider) })}</p>
        ) : null}
      </div>
    </SettingsCard>
  );
}
