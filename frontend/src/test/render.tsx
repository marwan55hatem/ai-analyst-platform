import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement, ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import ar from "../../messages/ar.json";
import en from "../../messages/en.json";

/** Render inside the providers a page gets: translations, React Query, tooltips. */
export function renderWithProviders(
  ui: ReactElement,
  { locale = "en", ...options }: { locale?: "en" | "ar" } & RenderOptions = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale={locale} messages={locale === "ar" ? ar : en} timeZone="UTC">
        <QueryClientProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </QueryClientProvider>
      </NextIntlClientProvider>
    );
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) };
}
