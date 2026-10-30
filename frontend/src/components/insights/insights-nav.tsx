"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";

import { withDataset } from "@/lib/active-dataset";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/insights", key: "feed" },
  { href: "/insights/forecast", key: "forecast" },
  { href: "/insights/segments", key: "segments" },
  { href: "/insights/churn", key: "churn" },
  { href: "/insights/what-if", key: "whatIf" },
] as const;

/** Sub-navigation of the insights section; it keeps the chosen dataset when switching. */
export function InsightsNav() {
  const t = useTranslations("insights.nav");
  const pathname = usePathname();
  const dataset = useSearchParams().get("dataset") ?? undefined;
  const nav = useRef<HTMLElement>(null);
  // On narrow screens the tabs scroll; keep the current one in view.
  useEffect(() => {
    nav.current?.querySelector("[aria-current=page]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [pathname]);
  return (
    <nav
      ref={nav}
      aria-label={t("label")}
      className="-mx-1 mb-6 overflow-x-auto overflow-y-hidden border-b [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <ul className="flex min-w-max gap-1 px-1">
        {TABS.map((tab) => {
          const active = pathname === tab.href;
          return (
            <li key={tab.href}>
              <Link
                href={withDataset(tab.href, dataset)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px inline-flex h-10 items-center border-b-2 px-3 text-sm whitespace-nowrap",
                  active
                    ? "border-primary font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {t(tab.key)}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
