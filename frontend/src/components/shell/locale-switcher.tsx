"use client";

import { Languages } from "lucide-react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { LOCALE_COOKIE, directionOf, toLocale, type Locale } from "@/i18n/locales";

/** Store the chosen language in a cookie and re-render the server tree. */
export function setLocaleCookie(locale: Locale): void {
  document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=31536000; samesite=lax`;
  document.documentElement.lang = locale;
  document.documentElement.dir = directionOf(locale);
}

/** One-tap switch to the other language, labelled in that language. */
export function LocaleSwitcher({ className }: { className?: string }) {
  const t = useTranslations("header");
  const locale = toLocale(useLocale());
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const next: Locale = locale === "ar" ? "en" : "ar";

  return (
    <Button
      variant="ghost"
      size="sm"
      className={className}
      disabled={pending}
      aria-label={`${t("language")}: ${t("switchTo")}`}
      onClick={() => {
        setLocaleCookie(next);
        startTransition(() => router.refresh());
      }}
    >
      <Languages aria-hidden="true" />
      <span lang={next}>{t("switchTo")}</span>
    </Button>
  );
}
