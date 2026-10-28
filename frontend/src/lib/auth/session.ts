/**
 * Access-token session kept in memory only (never in web storage).
 *
 * The refresh token is an httpOnly cookie scoped to /api/v1/auth that the backend
 * rotates on every refresh; presenting an already-rotated token revokes all of the
 * user's sessions (reuse detection). Refreshes are therefore serialised:
 *   - within a tab, concurrent callers share one in-flight promise;
 *   - across tabs, the request runs inside a Web Lock, so a second tab only refreshes
 *     after the first has received (and the browser has stored) the rotated cookie.
 */

export const REFRESH_LOCK = "analyst-auth-refresh";
const REFRESH_URL = "/api/v1/auth/refresh";

let accessToken: string | null = null;
let inflight: Promise<boolean> | null = null;
const expiredListeners = new Set<() => void>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** Forget the in-memory token (logout or a failed refresh). */
export function clearSession(): void {
  accessToken = null;
}

/** Subscribe to "the session could not be renewed"; returns an unsubscribe function. */
export function onSessionExpired(listener: () => void): () => void {
  expiredListeners.add(listener);
  return () => expiredListeners.delete(listener);
}

export function notifySessionExpired(): void {
  for (const listener of expiredListeners) listener();
}

async function requestNewToken(): Promise<boolean> {
  try {
    const response = await fetch(REFRESH_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      accessToken = null;
      return false;
    }
    const body = (await response.json()) as { access_token?: unknown };
    accessToken = typeof body.access_token === "string" ? body.access_token : null;
    return accessToken !== null;
  } catch {
    // Network failure: keep whatever token we had; the caller decides what to do.
    return false;
  }
}

function withCrossTabLock(task: () => Promise<boolean>): Promise<boolean> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (locks && typeof locks.request === "function") {
    return locks.request(REFRESH_LOCK, { mode: "exclusive" }, task).then((held) => held);
  }
  // Browsers without Web Locks still get the per-tab single flight below.
  return task();
}

/**
 * Exchange the refresh cookie for a new access token. Safe to call from many places at
 * once: one request per tab, one tab at a time. Resolves to whether a token is held.
 */
export function refreshSession(): Promise<boolean> {
  if (!inflight) {
    inflight = withCrossTabLock(requestNewToken).finally(() => {
      inflight = null;
    });
  }
  return inflight;
}
