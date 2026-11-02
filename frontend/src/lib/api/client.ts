import createClient from "openapi-fetch";

import {
  clearSession,
  getAccessToken,
  notifySessionExpired,
  refreshSession,
} from "@/lib/auth/session";

import type { paths } from "./schema";

/** Error envelope from the backend: {code, message, details}. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** Build an ApiError from any response body (JSON envelope or not). */
export function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "code" in body) {
    const env = body as { code: unknown; message?: unknown; details?: unknown };
    return new ApiError(
      status,
      String(env.code),
      typeof env.message === "string" ? env.message : "",
      env.details ?? null,
    );
  }
  return new ApiError(status, status === 0 ? "network_error" : "http_error", "");
}

const PUBLIC_AUTH_PATHS = ["/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/refresh"];

function withToken(request: Request, token: string | null): Request {
  const headers = new Headers(request.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return new Request(request, { headers });
}

/**
 * fetch that adds the bearer token and, on a 401, refreshes once and retries once.
 * A failed refresh clears the session and tells listeners (the app redirects to login).
 * `signal` lets a caller cancel it (the chat stream's Stop button).
 */
export async function authFetch(input: Request, init?: { signal?: AbortSignal }): Promise<Response> {
  const isPublic = PUBLIC_AUTH_PATHS.some((p) => new URL(input.url).pathname === p);
  const retry = input.clone();
  const sentToken = getAccessToken();
  const options = init?.signal ? { signal: init.signal } : undefined;
  const response = await fetch(withToken(input, sentToken), options);
  if (response.status !== 401 || isPublic) return response;

  // Another request may already have renewed the token while this one was in flight.
  const current = getAccessToken();
  const renewed = current !== null && current !== sentToken ? true : await refreshSession();
  if (!renewed) {
    clearSession();
    notifySessionExpired();
    return response;
  }
  return fetch(withToken(retry, getAccessToken()), options);
}

function baseUrl(): string {
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

export const api = createClient<paths>({
  baseUrl: baseUrl(),
  fetch: authFetch,
  credentials: "same-origin",
});

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Return data or throw an ApiError with the backend's code. */
export async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await promise;
  } catch {
    throw new ApiError(0, "network_error", "");
  }
  if (result.error !== undefined || !result.response.ok) {
    throw toApiError(result.response.status, result.error);
  }
  return result.data as T;
}
