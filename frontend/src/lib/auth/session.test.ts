import { delay, http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";

/**
 * A fake backend with the real rotation rule: each refresh revokes the presented token
 * and issues a new one; presenting a revoked token is "reuse" and logs the user out.
 * The browser cookie jar is shared by every tab, like a real browser profile.
 */
function rotatingBackend() {
  const state = { valid: "t0", jar: "t0", reuse: 0, calls: 0, loggedOut: false };
  server.use(
    http.post("*/api/v1/auth/refresh", async () => {
      state.calls += 1;
      const presented = state.jar; // the cookie the browser sends with this request
      await delay(20);
      if (state.loggedOut || presented !== state.valid) {
        state.reuse += presented !== state.valid ? 1 : 0;
        state.loggedOut = true;
        return HttpResponse.json({ code: "unauthorized", message: "", details: null }, { status: 401 });
      }
      state.valid = `t${state.calls}`;
      state.jar = state.valid; // Set-Cookie with the rotated token
      return HttpResponse.json({ access_token: `access-${state.calls}`, token_type: "bearer", expires_in: 1800 });
    }),
  );
  return state;
}

/** Minimal Web Locks: one exclusive holder per name, others wait in order. */
function installLocks() {
  const queues = new Map<string, Promise<unknown>>();
  const locks = {
    request: (name: string, _opts: unknown, cb: () => Promise<unknown>) => {
      const previous = queues.get(name) ?? Promise.resolve();
      const run = previous.then(() => cb());
      queues.set(name, run.catch(() => undefined));
      return run;
    },
  };
  Object.defineProperty(navigator, "locks", { value: locks, configurable: true });
}

function removeLocks() {
  Object.defineProperty(navigator, "locks", { value: undefined, configurable: true });
}

/** A fresh copy of the module: what a second browser tab has. */
async function newTab() {
  vi.resetModules();
  return import("./session");
}

describe("refreshSession", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => removeLocks());

  it("sends one request for concurrent callers in the same tab", async () => {
    const backend = rotatingBackend();
    const tab = await newTab();
    const results = await Promise.all([tab.refreshSession(), tab.refreshSession(), tab.refreshSession()]);
    expect(results).toEqual([true, true, true]);
    expect(backend.calls).toBe(1);
    expect(tab.getAccessToken()).toBe("access-1");
  });

  it("serialises two tabs with Web Locks so a rotated token is never reused", async () => {
    installLocks();
    const backend = rotatingBackend();
    const tabA = await newTab();
    const tabB = await newTab();
    const [a, b] = await Promise.all([tabA.refreshSession(), tabB.refreshSession()]);
    expect([a, b]).toEqual([true, true]);
    expect(backend.reuse).toBe(0);
    expect(backend.loggedOut).toBe(false);
    expect(backend.calls).toBe(2);
  });

  it("without Web Locks, two tabs racing trigger reuse detection (why the lock exists)", async () => {
    removeLocks();
    const backend = rotatingBackend();
    const tabA = await newTab();
    const tabB = await newTab();
    const results = await Promise.all([tabA.refreshSession(), tabB.refreshSession()]);
    expect(results).toContain(false);
    expect(backend.reuse).toBe(1);
  });

  it("clears the token when the refresh is refused", async () => {
    server.use(
      http.post("*/api/v1/auth/refresh", () =>
        HttpResponse.json({ code: "unauthorized", message: "", details: null }, { status: 401 }),
      ),
    );
    const tab = await newTab();
    tab.setAccessToken("stale");
    expect(await tab.refreshSession()).toBe(false);
    expect(tab.getAccessToken()).toBeNull();
  });
});
