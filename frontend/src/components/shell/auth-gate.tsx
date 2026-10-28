"use client";

import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth/auth-provider";

/**
 * Client-side route guard. The refresh cookie is scoped to /api/v1/auth, so server
 * middleware cannot see it; the browser restores the session and redirects if needed.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, expired } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations("common");

  useEffect(() => {
    if (status !== "anonymous") return;
    const params = new URLSearchParams({ next: pathname });
    if (expired) params.set("expired", "1");
    router.replace(`/login?${params.toString()}`);
  }, [status, expired, pathname, router]);

  if (status !== "authenticated") {
    return (
      <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]" aria-busy="true">
        <div className="hidden border-e bg-sidebar lg:block" />
        <div className="space-y-4 p-6 lg:p-10">
          <span className="sr-only" role="status">
            {t("loading")}
          </span>
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
