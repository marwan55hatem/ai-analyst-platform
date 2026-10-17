# Test plan – Step 3 (profiling, roles, quality, cleaning)

Risk first: the cleaning plan can change customers' data, and the LLM refinement is the
only place data could leave the platform. Both get the hardest tests. Profiling runs in
real DuckDB over real Parquet, API tests use real Postgres (Alembic-built) with Celery
in eager mode, as in Step 2. No test needs an API key.

| Area | What is proven | Type | Where |
| --- | --- | --- | --- |
| Identifier columns | `id/code/number/no/phone/zip/sku` (EN + Arabic) columns and leading-zero values (`00123`) stay TEXT in CSV (fast path and per-column fallback) and XLSX; look-alikes (`paid`, `discount`) stay numeric | unit | `test_identifier_ingest.py` |
| Messy numbers | currency, thousands separators, percentages, Arabic-Indic digits and Arabic decimal/thousands separators parse to the right numbers | unit | `test_profiling_parsing.py` |
| Column stats | null %, distinct, min/max/mean/median/std, quantiles, skew, top 10, IQR outliers equal values computed independently in Python | unit | `test_profiling_stats.py` |
| Type inference | integer/float/boolean/datetime/categorical/text/identifier, numbers stored as text, mixed types flagged with conforming share | unit | `test_profiling_stats.py` |
| Dates | several formats, dd/mm vs mm/dd decided or flagged ambiguous, range, daily/weekly/monthly granularity, gaps | unit | `test_profiling_dates.py` |
| PII | emails, phones, national-ID-like values flagged; masking never leaks the original; stored top values for PII columns are masked | unit | `test_profiling_pii.py` |
| Roles (rules) | >= 90% of planted roles correct on clean + messy fixtures, English and Arabic names; confidence is lower for weak matches | unit | `test_profiling_roles.py` |
| Roles (LLM) | answer accepted when consistent; answer conflicting with the profile discarded; user roles never overridden; **payload has names + <= 5 masked values per column, no PII values, no values for PII columns, no rows** | unit | `test_role_refinement.py` |
| Quality score | clean fixture >= 90, messy < 60, breakdown sums to the total, weights 30/25/15/15/15, top 5 issues have plain text + suggested fix, tiny data reported as low confidence | unit | `test_quality.py` |
| Cleaning plan | ids stable, affected counts correct, **only low-risk fixes are `recommended`**, dedupe is medium, ambiguous dd/mm is high, null strategy defaults to `keep`, outlier fix flags only | unit | `test_cleaning_plan.py` |
| Cleaning apply | each fix works; nothing medium/high runs unless listed; applying only low-risk ids keeps duplicates and nulls; score of the messy fixture rises >= 25 when the needed fixes are listed; original version row, file bytes and Parquet untouched; logs written; unknown fix id -> 422 | integration | `test_cleaning_apply.py` |
| Version flow | cleaned version goes through uploaded -> processing -> ready then is profiled; late (older) version does not move `current_version_id` back | integration | `test_cleaning_apply.py` |
| Profile stage | `profile_status` pending -> running -> ready/failed with its own error; a failing profile leaves the dataset downloadable and queryable | integration | `test_profile_task.py` |
| Rerun | ready and failed -> pending then re-profiled; running -> 409; `?version=N`; user role overrides survive | integration | `test_profile_api.py` |
| Stuck sweeper | profiles `running` longer than `INGEST_STUCK_MINUTES` fail with their own message; fresh ones and ingestion rules untouched | unit | `test_stuck_ingestion.py` |
| API | profile, quality, cleaning-plan, apply, role override: happy paths, 409 not ready/failed, 404 unknown column, 422 bad role | integration | `test_profile_api.py` |
| Tenant isolation | every new `{param}` route returns 404 cross-tenant and 401 anonymously | integration | `test_isolation.py` |
| Migration | upgrade/downgrade with new columns and tables | integration | `test_migration.py` |
| Memory | profiling uses DuckDB with a memory limit and never `read_csv`/`read_parquet` into pandas | unit | `test_profiling_stats.py` |
| Coverage gate | `app/services/profiling` >= 85% | gate | `scripts/check_coverage.py` |

Gaps accepted: leading zeros that first appear after the first 1 MB of a column whose name
is not identifier-like are not detected (documented in `known-limits.md`).
