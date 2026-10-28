"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/common/page-header";
import { LocaleSwitcher } from "@/components/shell/locale-switcher";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { useAuth } from "@/lib/auth/auth-provider";

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[12rem_1fr] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

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
      </div>
    </>
  );
}
