/** What the user can do about a failed answer. */
export type ErrorAction = "retry" | "wait" | "rephrase" | "settings" | "none";

export interface ChatErrorView {
  /** Key under `chat.errors`. */
  key: string;
  action: ErrorAction;
  seconds?: number;
}

const VIEWS: Record<string, Omit<ChatErrorView, "seconds">> = {
  budget_exceeded: { key: "budget", action: "settings" },
  provider_rate_limited: { key: "rateLimited", action: "wait" },
  rate_limited: { key: "rateLimited", action: "wait" },
  too_many_streams: { key: "tooManyStreams", action: "wait" },
  conversation_busy: { key: "busy", action: "retry" },
  provider_no_balance: { key: "noBalance", action: "none" },
  provider_auth_failed: { key: "providerAccess", action: "none" },
  provider_model_not_found: { key: "modelMissing", action: "none" },
  llm_not_configured: { key: "notConfigured", action: "none" },
  provider_unavailable: { key: "unavailable", action: "retry" },
  provider_timeout: { key: "timeout", action: "retry" },
  provider_error: { key: "unavailable", action: "retry" },
  provider_bad_request: { key: "badRequest", action: "retry" },
  provider_bad_tool_call: { key: "badToolCall", action: "retry" },
  provider_request_too_large: { key: "tooLarge", action: "rephrase" },
  internal_error: { key: "internal", action: "retry" },
  network_error: { key: "network", action: "retry" },
  not_found: { key: "gone", action: "none" },
  dataset_not_ready: { key: "datasetNotReady", action: "retry" },
  validation_error: { key: "invalid", action: "rephrase" },
};

export const CHAT_ERROR_CODES = Object.keys(VIEWS);

/** Map a backend error code to its message and next action; unknown codes can still retry. */
export function chatErrorView(code: string, retryAfterS?: number | null): ChatErrorView {
  const view = VIEWS[code] ?? { key: "generic", action: "retry" as const };
  if (view.key === "rateLimited" && !retryAfterS) return { ...view, key: "rateLimitedSoon" };
  return retryAfterS ? { ...view, seconds: retryAfterS } : view;
}
