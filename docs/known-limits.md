# Known limits

Accepted trade-offs of the current backend. Each has an owner step.

| Limit | Behaviour | Planned fix |
| --- | --- | --- |
| Redis-down rate limiter | If Redis is unreachable the limiter lets requests through and logs `rate_limiter_unavailable`. Logins stay available but are unthrottled. | Kept on purpose. |
| Register reveals emails | `POST /auth/register` returns 409 `email_taken` for an existing email, so registered addresses can be probed (limited to 5 registrations per IP per hour). | Kept on purpose. |
| Proxy IP and rate limits | `X-Forwarded-For` is ignored; limits use the direct peer IP. Behind a reverse proxy all users share one IP and per-IP limits (30 logins/min, 5 registers/hour, 30 refreshes/min) apply to everyone. | Step 14/15 (security, deployment): trusted-proxy handling. |
| Simultaneous refresh race | Two requests refreshing with the same cookie at once: the second sees a revoked token and revokes the user's whole token family, logging them out everywhere. | Step 11 (frontend): add a short grace window for the just-rotated token. |
| Type inference on uploads | Column types come from the first ~1 MB of a CSV (first 10,000 rows of an Excel sheet). If a later value does not fit (for example `$1,200` in a number column), the whole file is re-ingested with every column as text and the version gets an `all-strings` warning. No rows are lost, but the numeric columns then need casting in SQL. IDs with leading zeros (`00123`) are read as numbers when the sample looks numeric. | Step 3 (profiling and cleaning): per-column type suggestions and overrides. |
| Upload size check happens after spooling | Starlette spools the multipart body to a temp file before our streaming check enforces `MAX_UPLOAD_MB`, so an oversized request still costs temp disk and bandwidth before the 413. | Step 15 (deployment): reverse-proxy `client_max_body_size`. |
| Excel is read as values | `.xlsx` is read with `data_only=True`: formulas show their last cached value (empty if the file was never opened in Excel), merged cells keep the value only in the top-left cell, one sheet per version. | Kept on purpose; upload one version per sheet. |
| Soft delete keeps files | `DELETE /datasets/{id}` hides the dataset and blocks queries but keeps the original and Parquet files on disk. | Step 14 (security): retention and purge job. |
| Query layer has no HTTP route yet | `SafeQueryService` is used by preview and (later) the analysis engine and agent; there is no public SQL endpoint. Each query loads the Parquet into memory (`QUERY_MEMORY_MB`, default 512 MB; 256 MB in compose), so very wide files near the 2,000,000-row limit can hit that cap and fail with a clear error. | Step 4/8 decide whether to expose it. |
| Stuck `processing` after a worker crash | If the worker is killed mid-conversion the version stays `processing`; ingestion only starts from `uploaded` and nothing requeues it. Re-upload the file. | Step 14 (observability): stale-job sweeper in Celery beat. |
| Excel type inference | The first 10,000 data rows decide the column types; one later float in an integer column (or a first batch that is all empty) turns the whole sheet into text columns. | Step 3: type promotion and per-column overrides. |
| Encoding sniffed from the first 1 MB | Invalid bytes only after the first 1 MB of a non-UTF-8 file surface as a parse error instead of switching encoding. | Step 3. |
