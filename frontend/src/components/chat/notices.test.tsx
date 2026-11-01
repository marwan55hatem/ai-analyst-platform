import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { ChatError } from "./chat-error";
import { StatusNotice } from "./status-notice";

describe("StatusNotice", () => {
  beforeEach(() => vi.useFakeTimers({ now: 1_000_000 }));
  afterEach(() => vi.useRealTimers());

  it("counts down without a new announcement each second", () => {
    renderWithProviders(<StatusNotice until={1_000_000 + 40_000} />);
    expect(screen.getByTestId("status-notice")).toHaveTextContent("The model is busy, retrying in 40 s");
    const announced = screen.getByRole("status").textContent;
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByTestId("status-notice")).toHaveTextContent("retrying in 35 s");
    expect(screen.getByRole("status").textContent).toBe(announced);
  });

  it("stays calm when the wait runs over", () => {
    renderWithProviders(<StatusNotice until={1_000_000 + 1_000} />);
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByTestId("status-notice")).toHaveTextContent("The model is busy, retrying now");
    expect(screen.getByTestId("status-notice").textContent).not.toContain("-");
  });
});

describe("ChatError", () => {
  it("offers a retry after network loss", async () => {
    const onRetry = vi.fn();
    renderWithProviders(<ChatError error={{ code: "network_error", retryAfterS: null }} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The connection dropped");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("sends a spent budget to Settings, with no retry", () => {
    renderWithProviders(<ChatError error={{ code: "budget_exceeded", retryAfterS: null }} onRetry={vi.fn()} />);
    expect(screen.getByRole("link", { name: "See usage" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("gives the wait in seconds and holds the retry until it is over", () => {
    vi.useFakeTimers();
    renderWithProviders(<ChatError error={{ code: "provider_rate_limited", retryAfterS: 30 }} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 30 seconds");
    expect(screen.getByRole("button", { name: "Try again" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(31_000);
    });
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    vi.useRealTimers();
  });

  it("gives a long wait in minutes", () => {
    renderWithProviders(<ChatError error={{ code: "provider_rate_limited", retryAfterS: 1800 }} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 30 minutes");
  });

  it("offers nothing to press when only an administrator can fix it", () => {
    renderWithProviders(<ChatError error={{ code: "provider_no_balance", retryAfterS: null }} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ask your administrator");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("asks for a narrower question when the request is too large", () => {
    renderWithProviders(<ChatError error={{ code: "provider_request_too_large", retryAfterS: null }} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Ask about a shorter period");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("speaks Arabic", () => {
    renderWithProviders(<ChatError error={{ code: "network_error", retryAfterS: null }} onRetry={vi.fn()} />, {
      locale: "ar",
    });
    expect(screen.getByRole("button", { name: "حاول مرة أخرى" })).toBeInTheDocument();
  });
});
