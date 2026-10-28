import {
  clearSession,
  getAccessToken,
  notifySessionExpired,
  refreshSession,
} from "@/lib/auth/session";
import { ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES } from "@/lib/config";

import { ApiError, toApiError } from "./client";
import type { DatasetDetail } from "./types";

export type FileProblem = "wrong_type" | "empty" | "too_large";

/** Checks the browser can make before sending anything. */
export function checkFile(file: File): FileProblem | null {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(ext)) return "wrong_type";
  if (file.size === 0) return "empty";
  if (file.size > MAX_UPLOAD_BYTES) return "too_large";
  return null;
}

/** Dataset name suggested from a file name ("sales_2024.csv" -> "sales_2024"). */
export function nameFromFile(fileName: string): string {
  const base = fileName.replace(/\.[^.]+$/, "").trim();
  return base.slice(0, 200) || fileName.slice(0, 200);
}

interface UploadInput {
  file: File;
  name: string;
  description?: string;
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

function send(input: UploadInput, token: string | null): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/datasets");
    xhr.responseType = "json";
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) input.onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => resolve({ status: xhr.status, body: xhr.response });
    xhr.onerror = () => reject(new ApiError(0, "network_error", ""));
    xhr.onabort = () => reject(new ApiError(0, "aborted", ""));
    input.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    const form = new FormData();
    form.append("file", input.file);
    form.append("name", input.name);
    if (input.description) form.append("description", input.description);
    xhr.send(form);
  });
}

/** Upload a file as a new dataset, reporting progress; refreshes once on 401. */
export async function uploadDataset(input: UploadInput): Promise<DatasetDetail> {
  let result = await send(input, getAccessToken());
  if (result.status === 401) {
    if (!(await refreshSession())) {
      clearSession();
      notifySessionExpired();
      throw toApiError(401, result.body);
    }
    input.onProgress?.(0);
    result = await send(input, getAccessToken());
  }
  if (result.status < 200 || result.status >= 300) throw toApiError(result.status, result.body);
  return result.body as DatasetDetail;
}
