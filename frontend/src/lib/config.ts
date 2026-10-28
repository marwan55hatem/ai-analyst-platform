/**
 * Public, non-secret settings. NEXT_PUBLIC_ values are inlined at build time; nothing
 * secret may ever be added here.
 */

/** Product name shown in the UI; rename the product by changing this one variable. */
export const APP_NAME = process.env.NEXT_PUBLIC_APP_NAME || "Analyst";

/** Upload limit shown to users. Compose sets it from the same MAX_UPLOAD_MB as the API. */
export const MAX_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB || "50");

export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

export const ALLOWED_EXTENSIONS = ["csv", "tsv", "xlsx"] as const;
