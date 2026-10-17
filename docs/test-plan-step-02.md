# Test plan – Step 2 (datasets, ingestion, safe SQL)

Risk first: the SQL layer is reused by the analysis engine and the AI agent, so it is
tested hardest. Every test runs against real Postgres (Alembic-built) and real DuckDB/pyarrow.

| Area | What is proven | Where |
| --- | --- | --- |
| SQL abuse | 60+ malicious queries (DDL, DML, COPY, ATTACH, PRAGMA, INSTALL/LOAD, SET, file-reading functions, string table names, multi-statement, comment tricks, system functions, schema/catalog access, parameters) are rejected by the validator **and** by the service with a clear 422 | `test_query_service.py` |
| SQL defence in depth | Even if the validator were bypassed, the DuckDB connection refuses file reads, extension install/load, `SET` and config changes (external access off, autoload/autoinstall off, configuration locked) | `test_connection_is_locked_even_if_validation_were_bypassed` |
| SQL valid | 18 normal analytic queries (CTE, window, join, sample, unicode identifiers, quoted/upper-case `data`) run | `test_query_service.py` |
| SQL limits | row limit and truncation flag, clamping, default 1000, timeout interrupt (408), runtime errors become short 422 messages, JSON-safe values (NaN, Decimal, dates, bytes) | `test_query_service.py` |
| Tenant isolation | other tenant / unknown tenant / unknown version → 404; two tenants with different data never mix; every new route returns 404 cross-tenant; every non-public route is 401 anonymously; no endpoint accepts `tenant_id` (JSON or multipart) | `test_isolation.py`, `test_query_service.py` |
| Upload abuse | wrong extension, content/extension mismatch (xlsx↔csv, PNG/PDF/EXE bytes, NUL bytes), empty/blank, oversize (413, nothing left on disk), path-traversal filenames, corrupted/truncated/ZIP-only Excel, injected `tenant_id` form field | `test_datasets_api.py` |
| Formats | CSV, `;`, tab, `|`, UTF-8, UTF-8-BOM, UTF-16, Windows-1256 Arabic, XLSX with 2 sheets + sheet selection, messy headers (duplicates, symbols) | `test_datasets_api.py`, `test_ingest_unit.py` |
| Type safety | later-row type clash (`$1,200` on row 5000) restarts the file as all-text with a warning, no lost rows (CSV and XLSX) | `test_datasets_api.py` |
| Lifecycle | uploaded→processing→ready/failed, new versions keep the previous version usable, soft delete hides all routes and queries, queue outage marks failed, unexpected errors are not leaked | `test_datasets_api.py` |
| Roles | member uploads/reads, only owner/admin delete | `test_datasets_api.py` |
| Large files | a generated 105 MB CSV (tmp dir, never committed) is converted with Arrow peak < 90% of the file size and Python peak < 64 MB | `test_large_ingest.py` |
| Shared storage | compose mounts the same named volume at `STORAGE_DIR` in api and worker | `test_compose_storage.py` + manual check in the README of this step |
| Migration | `alembic upgrade head` / downgrade works with the new tables | `test_migration.py` |
| Coverage gate | `app/services/query.py` ≥ 90% (checked by `scripts/check_coverage.py`) | `make test` |
