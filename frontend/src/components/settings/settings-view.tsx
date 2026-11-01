"use client";

import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/common/page-header";
import { LocaleSwitcher } from "@/components/shell/locale-switcher";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { useAuth } from "@/lib/auth/auth-provider";

import { AiModelCard, AiUsageCard } from "./ai-usage-card";
import { PrivacyCard } from "./privacy-card";
import { Row } from "./settings-card";

export function SettingsView() {
  const t = useTranslations("settings");
  const { me } = useAuth();
  if (!me) return null;
  return (
    <>
      <PageHeader title={t("title")} />
      <div className="grid gap-6 xl:grid-cols-2">
        <section aria-labelledby="account-h" className="rounded-lg border bg-card p-6">
          <h2 id="account-h" className="text-lg font-semibold">
            {t("account")}
          </h2>
          <dl className="mt-2 divide-y">
            <Row label={t("name")}>
              <span dir="auto">{me.user.full_name}</span>
            </Row>
            <Row label={t("email")}>
              <span dir="ltr">{me.user.email}</span>
            </Row>
            <Row label={t("role")}>{t(`roles.${me.user.role}`)}</Row>
            <Row label={t("company")}>
              <span dir="auto">{me.tenant.name}</span>
            </Row>
          </dl>
        </section>
        <section aria-labelledby="prefs-h" className="rounded-lg border bg-card p-6">
          <h2 id="prefs-h" className="text-lg font-semibold">
            {t("preferences")}
          </h2>
          <dl className="mt-2 divide-y">
            <Row label={t("language")}>
              <LocaleSwitcher className="-ms-3" />
            </Row>
            <Row label={t("theme")}>
              <ThemeToggle />
            </Row>
          </dl>
        </section>
        <AiUsageCard />
        <AiModelCard />
        <PrivacyCard />
      </div>
    </>
  );
}
