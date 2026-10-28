import { ApiError } from "@/lib/api/client";

/** Backend error codes that have their own translated message under `errors.*`. */
export const KNOWN_ERROR_CODES = [
  "network_error",
  "unauthorized",
  "forbidden",
  "not_found",
  "rate_limited",
  "email_taken",
  "weak_password",
  "validation_error",
  "unsupported_media_type",
  "content_mismatch",
  "empty_file",
  "payload_too_large",
  "corrupt_file",
  "unknown_sheet",
  "dataset_not_ready",
  "dataset_failed",
  "profile_running",
  "profile_failed",
  "profile_not_ready",
  "analysis_running",
  "no_fixes_selected",
  "nothing_to_apply",
  "invalid_strategy",
  "unknown_fix",
  "internal_error",
] as const;

export type ErrorCode = (typeof KNOWN_ERROR_CODES)[number] | "generic";

/** The message key for an error; anything unrecognised gets a generic message. */
export function errorCode(error: unknown): ErrorCode {
  if (error instanceof ApiError) {
    if ((KNOWN_ERROR_CODES as readonly string[]).includes(error.code)) {
      return error.code as ErrorCode;
    }
    if (error.status === 401) return "unauthorized";
    if (error.status >= 500) return "internal_error";
  }
  return "generic";
}

/** Seconds to wait from a 429 envelope, if present. */
export function retryAfterSeconds(error: unknown): number | null {
  if (error instanceof ApiError && error.code === "rate_limited") {
    const details = error.details as { retry_after_seconds?: unknown } | null;
    const seconds = Number(details?.retry_after_seconds);
    return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : null;
  }
  return null;
}

/** Password-policy problems listed by the backend in a weak_password error. */
export function passwordProblems(error: unknown): string[] {
  if (error instanceof ApiError && error.code === "weak_password" && Array.isArray(error.details)) {
    return error.details.map(String);
  }
  return [];
}
