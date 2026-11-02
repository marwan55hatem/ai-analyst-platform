"use client";

import { useTranslations } from "next-intl";

import { ErrorState } from "@/components/common/error-state";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormat } from "@/lib/format";
import { useAiUsage } from "@/lib/queries";

import { Row, SettingsCard } from "./settings-card";

const NEAR_LIMIT = 0.8;
const PROVIDER_NAMES: Record<string, string> = { groq: "Groq", deepseek: "DeepSeek" };

export function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider;
}

/** Today's tokens against the company's daily budget, in words as well as a bar. */
export function AiUsageCard() {
  const t = useTranslations("settings.ai");
  const fmt = useFormat();
  const usage = useAiUsage();
  return (
    <SettingsCard id="ai-usage-h" title={t("usageTitle")}>
      {usage.isPending ? (
        <Skeleton className="mt-3 h-20 w-full" />
      ) : usage.error ? (
        <ErrorState title={t("loadFailed")} error={usage.error} onRetry={() => void usage.refetch()} />
      ) : (
        <div className="mt-3 space-y-2" data-testid="ai-usage">
          <p className="text-lg font-medium tabular-nums">
            {t("usedOf", { used: fmt.int(usage.data.tokens_used), budget: fmt.int(usage.data.daily_budget) })}
          </p>
          <Progress
            value={Math.min(100, Math.round((usage.data.tokens_used / Math.max(1, usage.data.daily_budget)) * 100))}
            aria-label={t("usageBar")}
          />
          {usage.data.tokens_used >= usage.data.daily_budget ? (
            <p className="text-sm font-medium text-warn">{t("reached")}</p>
          ) : usage.data.tokens_used >= usage.data.daily_budget * NEAR_LIMIT ? (
            <p className="text-sm font-medium text-warn">{t("near")}</p>
          ) : null}
          <p className="text-sm text-muted-foreground">{t("resets", { time: fmt.dateTime(usage.data.resets_at) })}</p>
          <p className="text-sm text-muted-foreground">{t("usageNote")}</p>
        </div>
      )}
    </SettingsCard>
  );
}

/** Provider and model names; keys never leave the server. */
export function AiModelCard() {
  const t = useTranslations("settings.ai");
  const usage = useAiUsage();
  const provider = usage.data?.provider;
  return (
    <SettingsCard id="ai-model-h" title={t("modelTitle")}>
      {usage.isPending ? (
        <Skeleton className="mt-3 h-12 w-full" />
      ) : usage.error ? (
        <ErrorState title={t("loadFailed")} error={usage.error} onRetry={() => void usage.refetch()} />
      ) : provider === "fake" || provider === "none" ? (
        <p className="mt-3">{provider === "fake" ? t("providerFake") : t("providerNone")}</p>
      ) : (
        <dl className="mt-2 divide-y" data-testid="ai-model">
          <Row label={t("provider")}>{providerName(usage.data.provider)}</Row>
          <Row label={t("model")}>
            <span dir="ltr">{usage.data.model}</span>
          </Row>
          {usage.data.model_strong !== usage.data.model ? (
            <Row label={t("modelStrong")}>
              <span dir="ltr">{usage.data.model_strong}</span>
            </Row>
          ) : null}
        </dl>
      )}
    </SettingsCard>
  );
}
