"use client";

import { authFetch, toApiError } from "@/lib/api/client";

/** Query string from defined values only. */
export function queryString(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  return q.toString();
}

/**
 * Download a CSV export with the session token (a plain link cannot send it). The file name
 * comes from the server's Content-Disposition, or the fallback.
 */
export async function downloadCsv(path: string, params: Record<string, string | number | undefined | null>, fallback: string) {
  const qs = queryString(params);
  const response = await authFetch(new Request(`${window.location.origin}${path}${qs ? `?${qs}` : ""}`));
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw toApiError(response.status, body);
  }
  const blob = await response.blob();
  const name = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? fallback;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
