"use client";

import { useCallback } from "react";

/**
 * Update query parameters on the current page. The native History API is used on purpose:
 * Next keeps useSearchParams in step with it, while router.replace would refetch the page from
 * the server and suspend it, which delays every filter by seconds and drops keyboard focus.
 * The browser's own query string is the source, so two quick updates both take effect.
 */
export function useUrlUpdate() {
  return useCallback((changes: Record<string, string | null>) => {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    const query = next.toString();
    window.history.replaceState(null, "", query ? `${window.location.pathname}?${query}` : window.location.pathname);
  }, []);
}
