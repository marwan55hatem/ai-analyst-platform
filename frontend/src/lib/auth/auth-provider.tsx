"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, unwrap } from "@/lib/api/client";
import type { Me } from "@/lib/api/types";

import {
  clearSession,
  getAccessToken,
  onSessionExpired,
  refreshSession,
  setAccessToken,
} from "./session";

type Status = "loading" | "authenticated" | "anonymous";

interface RegisterInput {
  company_name: string;
  full_name: string;
  email: string;
  password: string;
}

interface AuthValue {
  status: Status;
  me: Me | null;
  /** True when the session ended on its own (expired), not by logging out. */
  expired: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

async function fetchMe(): Promise<Me> {
  return unwrap(api.GET("/api/v1/auth/me"));
}

/** Holds the signed-in user; restores the session from the refresh cookie on load. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [expired, setExpired] = useState(false);
  const started = useRef(false);

  const signOutLocally = useCallback(() => {
    clearSession();
    setMe(null);
    setStatus("anonymous");
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      const ok = getAccessToken() !== null || (await refreshSession());
      if (!ok) {
        setStatus("anonymous");
        return;
      }
      try {
        setMe(await fetchMe());
        setStatus("authenticated");
      } catch {
        signOutLocally();
      }
    })();
  }, [signOutLocally]);

  useEffect(
    () =>
      onSessionExpired(() => {
        setExpired(true);
        signOutLocally();
      }),
    [signOutLocally],
  );

  const login = useCallback(async (email: string, password: string) => {
    const token = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
    setAccessToken(token.access_token);
    const profile = await fetchMe();
    setMe(profile);
    setExpired(false);
    setStatus("authenticated");
  }, []);

  const register = useCallback(
    async (input: RegisterInput) => {
      // Registering does not sign in; the API returns the profile only.
      await unwrap(api.POST("/api/v1/auth/register", { body: input }));
      await login(input.email, input.password);
    },
    [login],
  );

  const logout = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } finally {
      setExpired(false);
      signOutLocally();
    }
  }, [signOutLocally]);

  const value = useMemo(
    () => ({ status, me, expired, login, register, logout }),
    [status, me, expired, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
