import { renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";

import ar from "../../../messages/ar.json";
import en from "../../../messages/en.json";
import { useErrorMessage } from "./error-state";

function messageFor(error: unknown, locale: "en" | "ar" = "en"): string {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale={locale} messages={locale === "ar" ? ar : en}>
      {children}
    </NextIntlClientProvider>
  );
  return renderHook(() => useErrorMessage(), { wrapper }).result.current(error);
}

const limited = (seconds: number) => new ApiError(429, "rate_limited", "", { retry_after_seconds: seconds });

describe("useErrorMessage", () => {
  it("says seconds for a short wait and minutes for a long one", () => {
    expect(messageFor(limited(42))).toBe("Too many attempts. Try again in 42 seconds.");
    expect(messageFor(limited(3572))).toBe("Too many attempts. Try again in 60 minutes.");
    expect(messageFor(limited(3572), "ar")).toBe("محاولات كثيرة جدًا. حاول مرة أخرى بعد 60 دقيقة.");
  });

  it("translates known codes and hides unknown ones behind a generic message", () => {
    expect(messageFor(new ApiError(415, "content_mismatch", "x"))).toMatch(/contents don't match its extension/);
    expect(messageFor(new ApiError(400, "something_new", "x"))).toBe(
      "Something went wrong on our side. Try again in a moment.",
    );
    expect(messageFor(new ApiError(413, "payload_too_large", ""))).toMatch(/over the 50 MB limit/);
  });
});
