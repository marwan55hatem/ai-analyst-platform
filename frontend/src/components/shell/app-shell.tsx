"use client";

import {
  BarChart3,
  Database,
  FileText,
  Lightbulb,
  Menu,
  MessagesSquare,
  Settings,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import { Brand } from "@/components/common/brand";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useAuth } from "@/lib/auth/auth-provider";
import { cn } from "@/lib/utils";

import { LocaleSwitcher } from "./locale-switcher";
import { ThemeToggle } from "./theme-toggle";
import { UserMenu } from "./user-menu";

const NAV: { href: string; key: string; icon: LucideIcon }[] = [
  { href: "/datasets", key: "datasets", icon: Database },
  { href: "/dashboard", key: "dashboard", icon: BarChart3 },
  { href: "/insights", key: "insights", icon: Lightbulb },
  { href: "/chat", key: "chat", icon: MessagesSquare },
  { href: "/reports", key: "reports", icon: FileText },
  { href: "/settings", key: "settings", icon: Settings },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  return (
    <ul className="space-y-0.5">
      {NAV.map(({ href, key, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <li key={href}>
            <Link
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                active && "bg-card text-foreground shadow-[inset_3px_0_0_var(--primary)] rtl:shadow-[inset_-3px_0_0_var(--primary)]",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              {t(key)}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** Sidebar on the start side (it flips in RTL), header with company, language, theme, user. */
export function AppShell({ children }: { children: ReactNode }) {
  const t = useTranslations("nav");
  const th = useTranslations("header");
  const { me } = useAuth();
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:start-3 focus:top-3"
      >
        {t("skip")}
      </a>

      <aside className="hidden border-e border-sidebar-border bg-sidebar lg:block">
        <div className="sticky top-0 flex h-dvh flex-col gap-6 px-3 py-5">
          <Link href="/datasets" className="rounded-md px-3 text-lg">
            <Brand />
          </Link>
          <nav aria-label={t("main")}>
            <NavLinks />
          </nav>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:px-6">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t("openMenu")}>
                <Menu aria-hidden="true" />
              </Button>
            </SheetTrigger>
            <SheetContent side="start" className="w-72 bg-sidebar p-4">
              <SheetTitle className="px-3 pt-1 text-lg">
                <Brand />
              </SheetTitle>
              <nav aria-label={t("main")} className="mt-4">
                <NavLinks onNavigate={() => setOpen(false)} />
              </nav>
            </SheetContent>
          </Sheet>
          <Link href="/datasets" className="rounded-md lg:hidden">
            <Brand className="[&>span]:sr-only sm:[&>span]:not-sr-only" />
          </Link>

          {me ? (
            <p className="min-w-0 truncate text-sm">
              <span className="sr-only">{th("company")}: </span>
              <span className="font-medium" dir="auto">
                {me.tenant.name}
              </span>
            </p>
          ) : null}

          <div className="ms-auto flex items-center gap-1">
            <LocaleSwitcher />
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        <main id="main" tabIndex={-1} className="w-full max-w-[75rem] flex-1 px-4 py-6 outline-none sm:px-6 lg:px-10 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
