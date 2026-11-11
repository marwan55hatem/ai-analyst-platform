# Step 14: Security Hardening and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native, inline). Steps use `- [ ]` for tracking. Use superpowers:test-driven-development per task, superpowers:systematic-debugging for D3, security-audit for the threat model in F, superpowers:verification-before-completion before the report, superpowers:requesting-code-review on the squashed diff. Before coding a task, query the graphify MCP index (`query_graph` / `graphify_callers` / `graphify_tests_for`, repo `marwan55hatem/ai-analyst-platform`) for callers and linked tests of every function changed.

**Goal:** Make sure the app's protections actually hold (measured sandbox guarantees, customer-ID pseudonyms toward the model, web/API/upload/auth hardening, client IP, MLflow), add supply-chain scanning, make CI faster and add observability before deployment (Step 15).

**Architecture:** The model sees a pseudonymised data plane. `run_sql` queries a view whose customer_id column is already a pseudonym; the sandbox and stored-results tools emit pseudonyms. A server-side per-tenant/per-dataset mapping file turns pseudonyms back into real IDs only in evidence, streamed answer text, the UI and exports. Everything else is middleware, config, settings validation and tests.

**Spec:** the pasted Step 14 brief (sections A-F) plus the user's decisions below. Copy this plan to `docs/superpowers/plans/2026-11-10-step-14-security.md` in the first commit.

## Context
Steps 0-13 are merged. Open items: the sandbox isolation checks were never run as one suite against the container; raw customer_id values reach the model when the column is not PII-flagged (`run_sql`, `get_segments`, `get_churn_risk`, and the typed question); `PSEUDONYM_SECRET` silently falls back to a key derived from JWT_SECRET; MLflow has no auth; `client_ip` (`backend/app/api/deps.py:74`) uses the peer address, so every user behind Next shares one rate-limit bucket; Postgres and Redis publish on 0.0.0.0; xlsx has no zip-bomb or sheet/column limits; Celery has no structured logs or request ids; there is no /readyz and no metrics; backend CI takes 10-16 minutes; the Playwright reports Arabic keyboard test fails.

## User decisions (2026-11-10)
- customer_id is **always** pseudonymised toward the model, even with the allow_pii opt-in. The opt-in still covers the other PII columns.
- CSP: per-request nonce with `'strict-dynamic'` set in `frontend/src/proxy.ts`. Styles keep `'unsafe-inline'` and this is reported.
- MLflow: built-in `--app-name basic-auth` in `docker-compose.prod.yml`, no published port.
- Login throttle (revised): the strict lockout is keyed on **email + client IP**: 10 failures in 15 min → 429 + Retry-After for 15 min, and a correct password from that IP is refused during the block. A looser **per-email backstop** allows 50 failures per hour → 429 with the same generic message and Retry-After. A different IP can still log in while one IP is blocked.
- **Two PRs.** PR A, branch `step-14a-sandbox-pseudonyms`, holds Tasks 1-4 + Task 4b (live check) and is squashed to `feat: sandbox isolation suite and customer-id pseudonyms`. Stop when PR A is open with green CI and wait for "merge". PR B, branch `step-14b-hardening` created from main after A is merged, holds Tasks 5-12 and is squashed to `feat: security hardening and observability`.

## Global Constraints
- No real model calls. Every stack starts with `$env:LLM_PROVIDER='fake'; $env:GROQ_API_KEY='e2e-disabled'; $env:RATE_LIMIT_ENABLED='false'`, then `make up; make migrate`. The limiter is turned on only in the tests that check it.
- `.env` is never edited; GROQ_API_KEY is never printed. Plain `make down` only, never `-v`, and no volume or image removal.
- Branches: `step-14a-sandbox-pseudonyms` off main (Tasks 1-4b), then `step-14b-hardening` off the updated main (Tasks 5-12). Local checkpoint commit per task; each branch is squashed into its one commit before its first push. No merge by me. At most 3 CI fix attempts per PR, each a new commit.
- The only real model use is Task 4b. Its budget is 30K tokens; it stops if fewer than 40K tokens remain for the day.
- Coverage gates are not lowered (`backend/scripts/check_coverage.py`). Add `app/core` security modules to the guarded set where they are new.
- Errors use the `{code, message, details}` envelope. tenant_id comes only from the JWT.
- Every new Settings field goes in `.env.example` with a comment.

**Order:** PR A runs 1 → 2 → 3 → 4 → 4a → 4b, then waits for "merge". PR B runs 5 → 7 → 6 → 8 → 9 → 10 → 11 → 12; Task 7 comes before Task 6 because the login-throttle test needs a real client IP.

## Review Focus
1. Numeric customer IDs (e.g. `10234`): value-based replacement must never touch revenue or quantity numbers → covered because replacement happens on the customer_id column (view), never on cell values in general (test in Task 2).
2. A pseudonym split across two SSE text deltas must still be shown as the real ID → stream demasker test (Task 3).
3. A user types a real ID in lowercase or with surrounding punctuation ("customer c-001?") → normalised token lookup test (Task 3).
4. A forwarded-for chain with several hops, garbage entries or IPv6 → rightmost-untrusted parse test (Task 7).
5. An xlsx with one tiny compressed entry that expands to more than the cap → rejected before openpyxl opens it (Task 6).

---

### Task 1: Sandbox isolation verification suite (A)
**Files:** Create `sandbox/isolation_check.py` (runs inside a client container against the real socket and container), `backend/tests/test_sandbox_isolation.py` (pytest wrapper, skipped unless `SANDBOX_IT=1`). Modify `docker-compose.yml` (a `sandbox-isolation-check` service under profile `ci`, mounts `sandbox-ipc` only), `Makefile` (`sandbox-isolation` target), `.github/workflows/ci.yml`, and `sandbox/sandbox_server/*` only if a guarantee fails.
- [ ] Write one check per guarantee in `docs/agent-design.md:191-219`. Each check posts **real code through `/run` over the socket**, so the static guard is bypassed only where noted: a second mode sends code through `guard`-free `_spawn` to prove the deeper layers. Guarantees: no network (DNS and TCP connect to `postgres:5432`, `1.1.1.1:53`); read-only root (write `/app/x`, `/usr/x`); no storage volume (`/app/storage`, `/storage` absent); no app environment (`os.environ` has no `JWT_SECRET`, `DATABASE_URL`, `GROQ_API_KEY`, or `PSEUDONYM_SECRET`); memory (allocate 600 MB → `memory_limit`); CPU (busy loop → `cpu_limit` or `killed` within 17 s); processes (fork bomb → refused, container pid count back to baseline); file size (write 40 MB → refused); time limit (sleep 60 → killed at about 15 s); blocked modules and builtins (each entry of `BLOCKED_NAMES`, plus `open`, `eval` and `__import__('os')`); output cap (print 200 KB → truncated at 64 KB; result over 256 KB → error); recovery (after each of crash, OOM and hang, `GET /health` is ok and `result = 1+1` returns 2 within 2 s).
- [ ] Tenant separation: job A writes a marker file into its workdir and stores the workdir path in its result; job B (other tenant data) lists `/tmp`, tries to read A's path, and gets nothing; `/tmp` is empty between jobs; B's `data.csv` contains only B rows. Also a backend test that `build_data` for tenant B with tenant A's version id raises not-found.
- [ ] Each check prints `guarantee | expected | measured | PASS/FAIL` as a markdown table to stdout. CI saves it as an artifact; it is copied into docs/security.md.
- [ ] Run it locally: `docker compose --profile ci run --rm sandbox-isolation-check`. Fix any FAIL in the sandbox code or compose (likely candidates: `memswap_limit` missing on `sandbox`; pid baseline; env leakage) and keep the test.
- [ ] Commit `test: sandbox isolation suite against the real container`.

### Task 2: Pseudonym core + model-facing data plane (B1, B3)
**Files:** Create `backend/app/core/pseudonyms.py`, `backend/tests/test_pseudonyms.py`. Modify `backend/app/core/config.py`, `backend/app/agent/sandbox_client.py`, `backend/app/agent/tools/data.py`, `backend/app/agent/tools/findings.py`, `backend/app/services/query.py` (view hook), `backend/app/ml/modules/segments.py` and `churn.py` (customer_key uses the new pseudonym).
**Interfaces (produced):**
- `pseudonym(key: bytes, tenant_id: str, dataset_id: str, value: str) -> str`: `"CUST-" + crockford_base32(HMAC-SHA256(key, f"{tenant_id}|{dataset_id}|{value}"))[:8]`. 40 bits. On a collision inside one mapping, the colliding entries get 10 chars, and the build fails loudly if there is still a collision.
- `class PseudonymMap`: `.to_pseudonym(real: str) -> str | None`, `.to_real(p: str) -> str | None`, `.lookup_token(token: str) -> str | None` (case- and whitespace-normalised), `.pattern: re.Pattern` (`CUST-[0-9A-HJKMNP-TV-Z]{8,10}`).
- `load_pseudonym_map(db, tenant_id, version) -> PseudonymMap`: built from the distinct customer_id values of the version's parquet with DuckDB. It is cached as `storage/<tenant>/<dataset>/pseudonyms-<key_id>.parquet` (`key_id` = first 8 hex of SHA-256 of the key, so a key rotation rebuilds it) and kept in process with an LRU of 8.
- `Settings.pseudonym_key` is unchanged in shape. New `ensure_production_safe` rule: with `app_env == "production"` and `pseudonym_secret` empty, shorter than 32 characters, or equal to `change-me`, raise at startup in the api, worker and beat.
- [ ] Tests first:
  - stable per tenant+dataset;
  - tenant A ≠ tenant B for the same value;
  - format regex;
  - collision extension (forced with a tiny monkeypatched length);
  - map round-trip;
  - production refuses an empty or default secret (`test_config.py`).
- [ ] `run_sql`: `SafeQueryService` exposes the table through a view in which the customer_id role column is replaced by a join to the mapping parquet (`LEFT JOIN` on the real value, selecting the pseudonym under the same column name). A WHERE on a pseudonym therefore works, and joins and counts are unchanged. Model content and evidence both come from the pseudonymised query. Evidence rows are de-pseudonymised in Task 3.
- [ ] Sandbox `build_data`: always pseudonymise the customer_id role column (not only when PII-flagged), whatever the allow_pii setting.
- [ ] `get_segments` / `get_churn_risk`: `customer_key` stored = new pseudonym (written by `segments.py`/`churn.py` via the map). Legacy `cust_…` rows: the map also carries the legacy form (`cust_`+16 hex, per-tenant key) so it can map them back, and the tools translate legacy to new before the content reaches the model.
- [ ] Test `test_numeric_customer_ids_do_not_touch_numbers`: dataset with integer customer ids that equal some revenue values; SQL `SELECT customer_id, SUM(revenue)` → ids pseudonymised, sums untouched.
- [ ] Commit `feat: pseudonymise customer ids toward the model`.

### Task 3: Map back for display, typed IDs, verifier, exports (B2, B5)
**Files:** Create `backend/app/agent/demask.py`. Modify `backend/app/agent/loop.py`, `backend/app/agent/service.py`, `backend/app/agent/verifier.py`, `backend/app/api/v1/chat.py` (SSE writer), `backend/app/api/v1/analysis.py` and `backend/app/services/exports.py`, and the segments/churn list endpoints.
**Interfaces:** `class StreamDemasker(map)`: `.feed(delta: str) -> str` holds back a trailing partial `CUST-…` match; `.flush() -> str`. `demask_text(map, text) -> str`. `demask_evidence(map, evidence: dict) -> dict` (rows, stdout and result values). `pseudonymise_question(map, text) -> str` (tokenises on whitespace and punctuation and replaces any token whose normalised form is a real ID).
- [ ] The question is stored as typed (shown to the user); the model gets `pseudonymise_question(...)`; history replay uses the pseudonymised form.
- [ ] The answer is stored as the model wrote it (with pseudonyms). Both the SSE stream and the stored-answer API return demasked text. The verifier strips `map.pattern` matches from answer and sources before it extracts numbers.
- [ ] Evidence is demasked server-side before it is sent to the client and before it is stored. The model-facing `content` is never demasked.
- [ ] Segments and churn list, sort and CSV export endpoints map `customer_key` → real. The formula guard still applies after mapping. Search and sort by customer key work on the real value. Sorting by pseudonym is acceptable if the real value is too costly; record which one was used.
- [ ] Tests:
  - `test_no_real_customer_id_in_provider_requests`: a full chat turn with the Groq client on an `httpx.MockTransport` that records every request body. The scripted turn calls `run_sql`, `run_python`, `get_segments` and `get_churn_risk`, and the question contains a real ID. Assert that no planted real ID appears in any body, and that the pseudonym of the typed ID does appear.
  - The UI and API answer show the real ID.
  - The demasker handles a pseudonym split across deltas.
  - The verifier passes an answer that quotes a pseudonym.
  - The CSV export has real IDs and inert formulas.
  - The frontend Vitest: `how-i-got-this` renders real IDs from evidence.
- [ ] Commit `feat: map pseudonyms back for display and exports`.

### Task 4: Privacy copy and docs for pseudonyms (B4)
**Files:** `frontend/src/components/settings/privacy-card.tsx`, `frontend/messages/en.json`, `frontend/messages/ar.json` (`settings.ai.sent.customerIds` and a new `settings.ai.pseudonymNote`), `docs/agent-design.md`, `docs/known-limits.md`.
- [ ] EN wording: "Customer IDs, replaced by a stable code such as CUST-7F3K9QAB. The real IDs stay on our servers; you still see them here." Give the Arabic equivalent and list every new or changed Arabic string in the final report.
- [ ] Document key rotation in agent-design.md. After rotation:
  - every pseudonym changes;
  - the mapping files rebuild;
  - stored segments and churn rows show the legacy form until the analysis re-runs;
  - old chat answers keep the old codes. They are demasked only while the old map file exists, and the old map is kept until a re-run.
- [ ] Commit `docs: what reaches the model with pseudonyms`.

### Task 4a: PR A finish
- [ ] Run on the fake stack: `make lint`, `make test TEST_WORKERS=4` (gates), the frontend lint, typecheck, test and build targets, the Playwright chat, insights and settings specs plus the full suite, and the sandbox isolation suite. Manual walkthrough on the fake stack: upload `sales_large_clean.csv`, run a chat turn, check that the stored provider request carries pseudonyms and the API answer carries real IDs, and check the segments/churn pages and CSV.
- [ ] `docs/security.md` gets a first section with the sandbox table; it is completed in PR B. CLAUDE.md progress: `Step 14a`.
- [ ] Squash: `git reset --soft main` → `feat: sandbox isolation suite and customer-id pseudonyms`. Push, run `gh pr create` and `gh pr checks --watch`. Do not merge.

### Task 4b: Live Groq check on PR A (user-requested, the only real model use)
- [ ] Budget gate as in Step 8c:
  - estimate today's Groq use from `messages` rows with `model LIKE 'openai/%'` created today (UTC);
  - confirm with the rate-limit headers of the first response (`_note_window` in `backend/app/llm/openai_compatible.py`);
  - stop before starting, or between questions, if fewer than 40K tokens remain;
  - set `AGENT_DAILY_TOKEN_BUDGET` on the api so the app itself stops at 30K tokens for this check.
- [ ] Switch only the api to Groq (`$env:LLM_PROVIDER='groq'`, unset the `e2e-disabled` override, `docker compose up -d --no-deps api`). The worker stays fake. Client TPM pacing keeps it under 8K tokens per minute; wait between questions if the window is low.
- [ ] Questions, on `sales_large_clean.csv` (after automatic analysis):
  1. total net revenue;
  2. top 5 customers by revenue;
  3. customers at risk of leaving;
  4. a question containing a real customer ID. **Ask the user to type the ID**, as they requested ("typed by me"); offer a real ID taken from the dataset as a suggestion.
- [ ] For each question, record: the answer (as the API returns it), the tools called, the verifier result, the real IDs shown in the UI/API versus only pseudonyms in the recorded provider request (captured by a request-logging hook in the transport for this run only, written to the scratchpad, never committed), and tokens in/out.
- [ ] Token overhead: tokenise the provider request bodies with the `CUST-…` codes vs the same bodies with the real IDs put back (tiktoken `o200k_base` as an approximation) and report the difference in tokens and percent.
- [ ] Save the results to `evaluation/results/step-14a-live-pseudonyms.md` and add them to the PR A body.
- [ ] Switch the shell back to fake (`$env:LLM_PROVIDER='fake'; $env:GROQ_API_KEY='e2e-disabled'`), restart the api, and confirm with `get_settings().llm_provider` that both api and worker report `fake`. Then `make down`.
- [ ] Wait for the user's "merge" (workflow rule 7), then create `step-14b-hardening` from the updated main.

### Task 5: Security headers, CORS and cookies (C1, C2)
**Files:** Create `frontend/src/proxy.ts` (Next 16 proxy, formerly middleware), `backend/app/core/security_headers.py`, `backend/tests/test_security_headers.py`, `frontend/e2e/headers.spec.ts`. Modify `frontend/next.config.ts`, `frontend/src/app/layout.tsx` and `frontend/src/components/providers.tsx` (pass the nonce to next-themes), `backend/app/main.py`, `backend/app/api/v1/auth.py`.
- [ ] Check the Next 16 nonce CSP docs with context7 first.
- [ ] Frontend CSP: `default-src 'self'; script-src 'self' 'nonce-{n}' 'strict-dynamic'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'`. In dev only, also allow `'unsafe-eval'` and the HMR websocket.
- [ ] Frontend headers: HSTS `max-age=31536000; includeSubDomains` only when `NODE_ENV=production` and `ENABLE_HSTS=true`. Keep nosniff, `X-Frame-Options: DENY`, Referrer-Policy `strict-origin-when-cross-origin`, and Permissions-Policy.
- [ ] API: a middleware adds nosniff, `X-Frame-Options DENY`, `CSP default-src 'none'; frame-ancestors 'none'`, Referrer-Policy `no-referrer`, and Permissions-Policy. It adds HSTS when `app_env == production`. `/docs` keeps working in dev (relaxed CSP for the docs routes only, and only outside production).
- [ ] CORS and cookies:
  - Production refuses `http://` origins other than localhost.
  - Production requires `cookie_secure=True`.
  - The refresh cookie becomes `samesite="strict"`; it is same-origin through the Next proxy. If an e2e test shows this breaks, keep `lax` and record why.
  - Tests: cookie flags, preflight from an allowed vs a disallowed origin, no `Access-Control-Allow-Origin` for an unknown origin.
- [ ] Playwright `headers.spec.ts`: the CSP header is present and there are no `securitypolicyviolation` events on every page in EN and AR. The full suite and axe stay green.
- [ ] Commit `feat: security headers and strict cookie policy`.

### Task 6: Authorization matrix, uploads, authentication (C3, C4, C5)
**Files:** Create `backend/tests/test_authz_matrix.py`, `backend/tests/test_upload_hardening.py`, `backend/tests/test_auth_hardening.py`, `backend/tests/fixtures_crafted.py` (builds crafted files in tmp). Modify `backend/app/services/upload_validation.py`, `backend/app/services/ingest.py`, `backend/app/services/profiling/engine.py`, `backend/app/agent/sandbox_client.py` (DuckDB `enable_external_access=false`, `lock_configuration`), `backend/app/core/config.py`, `backend/app/api/v1/auth.py`, `backend/app/services/auth.py`.
- [ ] Authorization matrix:
  - `POLICY: dict[tuple[method, path], Rule]` with `Rule(owner, admin, member, sample_body)`, where each role is `"allow"` or `"deny"`.
  - The test walks `app.openapi()` and fails on any route with no entry.
  - Per role it asserts: allow → status not in {401, 403}; deny → 403; unauthenticated → 401 (public routes listed explicitly); other tenant → 404 on every `{id}` route, using real ids from the first tenant.
- [ ] Uploads:
  - Before `testzip` and openpyxl, inspect `ZipFile.infolist()`. Caps: total uncompressed ≤ `XLSX_MAX_UNCOMPRESSED_MB=500`, per-entry ratio ≤ 100, entries ≤ 10,000.
  - Sheets ≤ `XLSX_MAX_SHEETS=50`; columns ≤ `MAX_COLUMNS=500` (CSV and xlsx); the rows cap is existing.
  - Each limit returns a specific error code.
  - Tests use crafted small files: a zip bomb with a 1 KB entry that expands to 600 MB of zeros, written with deflate; 51 sheets; 501 columns; a CSV that is really a PNG; a CSV with `=HYPERLINK` cells (inert in the CSV export); a filename with `../`, RTL override and control characters.
  - Audit every export path and apply `csv_safe` everywhere: churn and segments CSV, evidence table copy and CSV in the frontend chat, report tables.
- [ ] Authentication:
  - Strict lockout key `login-fail:{email}:{ip}`: 10 failures per 15 min. While blocked, return 429 + Retry-After for 15 min, and refuse even a correct password from that IP. A success resets the failure count for that email+IP.
  - Backstop key `login-fail-email:{email}`: 50 failures per hour → 429 with the same generic message and Retry-After.
  - Test `test_other_ip_can_log_in_while_one_ip_is_blocked`: block from IP A, then the correct password from IP B returns 200 (IP set through the trusted-proxy XFF from Task 7, or a patched `client_ip` if Task 7 is not done yet; do Task 7 before Task 6).
  - Add limits to logout, password change and team user creation (if those endpoints exist; list which ones do).
  - Tests: lockout, Retry-After, generic message, rotation still works, reuse revokes the token family, password policy table (short, common, contains email, only 3 unique characters, 129 characters).
- [ ] Commit `feat: authz matrix, upload limits and login throttling`.

### Task 7: Trusted-proxy client IP (C6)
**Files:** `backend/app/api/deps.py`, `backend/app/core/config.py` (`TRUSTED_PROXIES`: comma list of CIDRs and hostnames, default `frontend` in compose, empty in tests), `backend/app/core/client_ip.py`, `backend/tests/test_client_ip.py`, `docker-compose.yml`.
- [ ] `client_ip(request) -> str`:
  - If the peer is not trusted, return the peer.
  - Otherwise walk `X-Forwarded-For` right to left, skip trusted hops, and return the first untrusted valid IP.
  - If none is found, return the peer.
  - Hostnames resolve with a 60 s cache.
- [ ] Check whether the Next rewrite proxy appends `x-forwarded-for`. If it does not, `proxy.ts` sets it from `request` (the headers it forwards override client-supplied ones).
- [ ] Tests:
  - a spoofed XFF from an untrusted peer is ignored;
  - a trusted peer with a chain `spoof, real` returns `real`;
  - IPv6 and garbage entries are handled;
  - two users behind the proxy get separate rate-limit buckets: integration test against the stack with the limiter on, using curl with different XFF through port 3000.
- [ ] Commit `feat: real client ip from a trusted proxy`.

### Task 8: MLflow and ports (C7)
**Files:** `docker-compose.yml` (bind postgres, redis and api to `127.0.0.1`; mlflow stays localhost), new `docker-compose.prod.yml` (no published ports except frontend; mlflow with `--app-name basic-auth` and `mlflow[auth]` deps, admin credentials from env; redis `requirepass`), `backend/app/core/config.py` (MLFLOW_TRACKING_USERNAME/PASSWORD passed through; production refuses an MLflow URI without credentials), `.env.example`, `docs/security.md`.
- [ ] Verify with `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`: no ports other than 3000 are published, and an unauthenticated MLflow request returns 401 (bring the prod mlflow up briefly).
- [ ] Commit `feat: mlflow auth and minimal published ports`.

### Task 9: Observability (E)
**Files:** `backend/app/core/middleware.py` (validate inbound X-Request-ID `^[A-Za-z0-9-]{8,64}$`, else generate one), `backend/app/core/logging.py` (redaction processor), `backend/app/workers/celery_app.py` (`configure_logging`; `before_task_publish` puts `request_id` in headers; `task_prerun` binds it), create `backend/app/api/health.py` (`/healthz`, `/readyz` at the root), create `backend/app/core/metrics.py` and `backend/app/api/internal.py` (`GET /internal/metrics`), tests `test_observability.py` and `test_log_privacy.py`.
- [ ] `/healthz`: process alive. `/readyz`: DB `SELECT 1`, Redis ping, storage write+delete, sandbox `GET /health` (api only), MLflow `/health` (only when the tracking URI is http). Returns 200 or 503 with per-check `{ok, ms}`. No secrets in the response.
- [ ] Metrics are Redis-backed so the api and worker processes share them: request counts by route template and status, latency histogram buckets, analysis and report durations, LLM tokens by provider, rate-limit hits by key prefix. Output is Prometheus text format. The endpoint needs `Authorization: Bearer $METRICS_TOKEN` (constant-time compare). It returns 404 when the token is unset and is not reachable through the Next rewrite (it lives outside `/api/v1`).
- [ ] The redaction processor masks:
  - email addresses;
  - values of keys matching `password|token|secret|authorization|api_key|cookie`;
  - Bearer tokens;
  - the exact values of configured secrets.
- [ ] `test_log_privacy`: capture all log output across a full fake chat turn plus an upload and a login. Assert that no planted customer ids, emails, `GROQ_API_KEY`, JWT secret or `PSEUDONYM_SECRET` appear. Also assert that a Celery task's log line carries the request_id of the request that enqueued it.
- [ ] Commit `feat: request ids, health checks and internal metrics`.

### Task 10: Supply chain and CI speed (D1, D2)
**Files:** `.github/workflows/ci.yml`, new `.github/workflows/security.yml` (or extra jobs), `.gitleaks.toml` if needed, `docs/security.md` (triage).
- [ ] First run pip-audit (`uvx pip-audit` against the `uv export` requirements), `npm audit --omit=dev --audit-level=high` and gitleaks locally. Triage each finding in docs/security.md as fixed, upgraded, or accepted with a reason.
- [ ] Add CI jobs for pip-audit, npm audit and `gitleaks/gitleaks-action` (full history). They are `continue-on-error: true` while findings remain. Note which are blocking.
- [ ] Measure the baseline: `gh run list --workflow ci.yml --limit 10 --json` gives job durations. Also run `pytest --durations=30` locally.
- [ ] Speed-ups, without changing the coverage gates:
  - split lint (ruff + mypy) into its own parallel job;
  - move the sandbox build and container checks into their own job with buildx `cache-from/to: type=gha`;
  - cache the apt packages (`awalsh128/cache-apt-pkgs-action`);
  - if pytest is still over about 7 minutes, shard pytest into 2 matrix jobs by file (`pytest-split` or a simple modulo of `--collect-only`) that upload `.coverage.*`, plus a `coverage` job that combines them and runs `check_coverage.py`;
  - mark `test_large_sample.py` as `serial`.
- [ ] Report the before and after times from real CI runs. Target: under 10 minutes wall clock.
- [ ] Commit `ci: audits, secret scan and faster backend job`.

### Task 11: Playwright reports Arabic keyboard test (D3)
**Files:** `frontend/e2e/reports.spec.ts`, and possibly `frontend/src/components/reports/generate-dialog.tsx`.
- [ ] Reproduce on the fake stack (`npx playwright test e2e/reports.spec.ts`) and read the trace. Follow systematic-debugging, starting with these hypotheses: Radix RadioGroup has no `dir`, so ArrowRight goes the wrong way in RTL or wraps; a focus race with the Dialog auto-focus; `report_busy`/limit 429s from reused users.
- [ ] Fix the root cause. If it is RTL arrow direction, pass `dir` from the locale to RadioGroup and make the test wait for `toBeFocused()` and use a direction-correct key. If it is a 429, give the spec a fresh user. Write the cause in the report.
- [ ] Commit `fix: reports keyboard language test`.

### Task 12: Docs, full verification, squash, PR (F)
**Files:** create `docs/security.md`. Update `docs/known-limits.md`, `CLAUDE.md` (Step 14 log line and new commands), `.env.example`.
- [ ] Write docs/security.md with the security-audit skill as a guide. It covers:
  - assets: tenant data, customer ids, credentials, API keys;
  - trust boundaries: browser→Next→API, API→sandbox, API→LLM, worker→MLflow;
  - a table of each protection, what it covers and what it does not cover;
  - the sandbox table from Task 1;
  - the production configuration checklist: every required env var and which startup checks enforce it;
  - CSP leftovers (`style-src 'unsafe-inline'`);
  - known gaps: shared sandbox uid, no WAF, metrics only in Redis, and so on.
- [ ] Verification, all with the fake-model environment:
  - `make lint`, `make test TEST_WORKERS=4`, all with coverage gates;
  - the frontend lint, typecheck, test and build targets;
  - `make up; make migrate`, then the full Playwright suite (`make frontend-e2e`, with the Groq guard active);
  - `npm --prefix frontend run lighthouse`: dashboard Performance must not drop below the 44-47 baseline, and accessibility stays 100;
  - the manual walkthrough with real outputs: headers via curl; /readyz; /internal/metrics with and without the token; a spoofed XFF; a zip-bomb upload; a lockout; a chat turn whose stored provider request has no real IDs.
- [ ] `make down`, check that no background processes are left, and leave temp files only in the scratchpad.
- [ ] `git reset --soft main` on `step-14b-hardening`, then one commit `feat: security hardening and observability` with the attribution trailer. Push `-u origin step-14b-hardening`, run `gh pr create` (body as in the CLAUDE.md workflow rule 4), and `gh pr checks --watch`. Do not merge.
- [ ] Final report: what changed, test and coverage numbers, the sandbox table, CI before and after, guarantees that did not hold and how they were fixed, decisions for the user, the new Arabic strings, known gaps.

## Verification summary
End to end on the fake stack. The backend suite includes the mock-transport "no real ID in provider bodies" test and the log-privacy scan. The sandbox isolation suite runs against the real container (CI artifact table). The authz matrix is generated from OpenAPI. Playwright runs in full, including the headers spec with no CSP violations and the fixed reports keyboard test. Lighthouse runs on /dashboard. CI is green within 3 attempts.
