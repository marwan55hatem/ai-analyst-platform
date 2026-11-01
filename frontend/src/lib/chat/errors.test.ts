import { describe, expect, it } from "vitest";

import en from "../../../messages/en.json";
import { chatErrorView, CHAT_ERROR_CODES } from "./errors";

describe("chatErrorView", () => {
  it.each(CHAT_ERROR_CODES)("%s has its own message", (code) => {
    const view = chatErrorView(code);
    expect(view.key).not.toBe("generic");
    expect((en.chat.errors as Record<string, string>)[view.key]).toBeTruthy();
  });

  it("covers every code the backend documents", () => {
    expect(CHAT_ERROR_CODES).toEqual(
      expect.arrayContaining([
        "budget_exceeded",
        "provider_rate_limited",
        "provider_no_balance",
        "provider_auth_failed",
        "provider_unavailable",
        "provider_timeout",
        "provider_bad_request",
        "provider_request_too_large",
        "provider_model_not_found",
        "provider_bad_tool_call",
        "provider_error",
        "llm_not_configured",
        "internal_error",
        "not_found",
        "network_error",
        "rate_limited",
        "too_many_streams",
        "conversation_busy",
      ]),
    );
  });

  it("falls back for an unknown code and offers a retry", () => {
    expect(chatErrorView("something_new")).toEqual({ key: "generic", action: "retry" });
  });

  it("carries the seconds of a rate limit", () => {
    expect(chatErrorView("provider_rate_limited", 90)).toEqual({ key: "rateLimited", action: "wait", seconds: 90 });
  });

  it("has a version of the rate limit message without a time", () => {
    expect(chatErrorView("rate_limited").key).toBe("rateLimitedSoon");
  });

  it("sends a spent budget to settings instead of offering a retry", () => {
    expect(chatErrorView("budget_exceeded").action).toBe("settings");
  });

  it("asks for a narrower question when the request is too large", () => {
    expect(chatErrorView("provider_request_too_large").action).toBe("rephrase");
  });

  it("offers no retry when only an administrator can fix it", () => {
    for (const code of ["provider_no_balance", "provider_auth_failed", "llm_not_configured"]) {
      expect(chatErrorView(code).action).toBe("none");
    }
  });

  it("offers a retry after network loss", () => {
    expect(chatErrorView("network_error").action).toBe("retry");
  });
});
