import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { server } from "@/test/server";

import { getAccessToken, onSessionExpired, setAccessToken } from "../auth/session";
import { ApiError, api, unwrap } from "./client";

afterEach(() => setAccessToken(null));

describe("authFetch", () => {
  it("sends the bearer token, refreshes once on 401 and retries", async () => {
    setAccessToken("old");
    const seen: (string | null)[] = [];
    let refreshes = 0;
    server.use(
      http.get("*/api/v1/auth/me", ({ request }) => {
        const auth = request.headers.get("Authorization");
        seen.push(auth);
        if (auth !== "Bearer new") {
          return HttpResponse.json({ code: "unauthorized", message: "", details: null }, { status: 401 });
        }
        return HttpResponse.json({ user: { full_name: "A" }, tenant: { name: "T" } });
      }),
      http.post("*/api/v1/auth/refresh", () => {
        refreshes += 1;
        return HttpResponse.json({ access_token: "new", token_type: "bearer", expires_in: 1800 });
      }),
    );
    const me = await unwrap(api.GET("/api/v1/auth/me"));
    expect(me.tenant.name).toBe("T");
    expect(seen).toEqual(["Bearer old", "Bearer new"]);
    expect(refreshes).toBe(1);
    expect(getAccessToken()).toBe("new");
  });

  it("ends the session when the refresh fails", async () => {
    setAccessToken("old");
    let expired = 0;
    const stop = onSessionExpired(() => (expired += 1));
    server.use(
      http.get("*/api/v1/datasets", () =>
        HttpResponse.json({ code: "unauthorized", message: "", details: null }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/refresh", () =>
        HttpResponse.json({ code: "unauthorized", message: "", details: null }, { status: 401 }),
      ),
    );
    await expect(unwrap(api.GET("/api/v1/datasets"))).rejects.toMatchObject({ status: 401 });
    expect(expired).toBe(1);
    expect(getAccessToken()).toBeNull();
    stop();
  });

  it("does not refresh for a failed login", async () => {
    let refreshes = 0;
    server.use(
      http.post("*/api/v1/auth/login", () =>
        HttpResponse.json({ code: "unauthorized", message: "bad", details: null }, { status: 401 }),
      ),
      http.post("*/api/v1/auth/refresh", () => {
        refreshes += 1;
        return HttpResponse.json({});
      }),
    );
    const error = await unwrap(
      api.POST("/api/v1/auth/login", { body: { email: "a@b.co", password: "x" } }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("unauthorized");
    expect(refreshes).toBe(0);
  });

  it("maps the error envelope", async () => {
    server.use(
      http.get("*/api/v1/datasets", () =>
        HttpResponse.json({ code: "rate_limited", message: "slow", details: { retry_after_seconds: 7 } }, { status: 429 }),
      ),
    );
    await expect(unwrap(api.GET("/api/v1/datasets"))).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
      details: { retry_after_seconds: 7 },
    });
  });
});
