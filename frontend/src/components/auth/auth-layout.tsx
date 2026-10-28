"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";

import { Brand } from "@/components/common/brand";
import { LocaleSwitcher } from "@/components/shell/locale-switcher";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { useAuth } from "@/lib/auth/auth-provider";

/** Only same-site paths are accepted as a post-login destination. */
export function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/datasets";
}

const STEPS = ["upload", "health", "analyse"] as const;

/** The three real steps of the first journey, shown beside the form on wide screens. */
function HowItWorks() {
  const t = useTranslations("auth");
  return (
    <section aria-labelledby="how-h" className="max-w-md space-y-8">
      <p className="text-2xl leading-snug font-semibold text-balance">{t("tagline")}</p>
      <div className="space-y-4">
        <h2 id="how-h" className="text-sm font-medium text-muted-foreground">
          {t("stepsTitle")}
        </h2>
        <ol className="relative space-y-6 border-s border-dashed border-input ps-8">
          {STEPS.map((step, i) => (
            <li key={step} className="relative">
              <span
                aria-hidden="true"
                className="absolute -start-[2.85rem] top-0 grid size-7 place-items-center rounded-full border-2 border-primary bg-background text-xs font-semibold text-primary tabular-nums"
              >
                {i + 1}
              </span>
              <p className="font-medium">{t(`steps.${step}.title`)}</p>
              <p className="text-sm text-muted-foreground">{t(`steps.${step}.body`)}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/** Auth pages: the journey on the start side (wide screens), the form on the end side. */
export function AuthLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("auth");
  const { status } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    if (status === "authenticated") router.replace(safeNext(params.get("next")));
  }, [status, params, router]);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-between px-4 py-4 sm:px-8">
        <Brand className="text-lg" />
        <div className="flex items-center gap-1">
          <LocaleSwitcher />
          <ThemeToggle />
        </div>
      </header>
      <main
        id="main"
        className="mx-auto grid w-full max-w-5xl flex-1 items-start gap-12 px-4 pt-6 pb-16 sm:px-8 sm:pt-12 lg:grid-cols-[1fr_26rem] lg:pt-20"
      >
        <div className="hidden lg:block">
          <HowItWorks />
        </div>
        <div className="w-full max-w-[26rem] justify-self-center lg:justify-self-end">
          <p className="mb-6 text-muted-foreground lg:hidden">{t("tagline")}</p>
          <div className="rounded-lg border bg-card p-6 sm:p-8">{children}</div>
        </div>
      </main>
    </div>
  );
}
