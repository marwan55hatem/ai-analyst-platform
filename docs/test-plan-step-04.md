# Test plan – Step 4 (analytics pipeline)

Risk first: a wrong number that looks authoritative. So the synthetic dataset has facts computed
independently in plain Python and every module must recover them exactly, in English and in
Arabic column names. Tests use real Postgres (Alembic-built), real DuckDB and Celery in eager mode;
`AUTO_ANALYZE` is off in the suite and switched on only for the chain test.

| Area | What is proven | Type | Where |
| --- | --- | --- | --- |
| Ground truth | total revenue to the cent, orders, units, margin, top product/region, December peak (STL), year-to-date, dead-stock list, first cohort, month-1 retention; English and Arabic headers | integration | `test_analysis_ground_truth.py` |
| Ranking | scores 0-100 sorted high to low; every insight has confidence and sample size; filters by module, type, confidence | integration | `test_analysis_ground_truth.py` |
| Evidence | every stored SQL passes `validate_sql` and, run through `SafeQueryService`, returns the stored rows | integration | `test_analysis_pipeline.py` |
| Capabilities | all modules available; no `customer_id` gives the plain-language reason; no revenue/quantity/price skips the revenue modules | integration | `test_analysis_pipeline.py`, `test_ml_units.py` |
| Missing columns | a run without `customer_id` completes with cohorts skipped and explained; revenue derived as quantity x price matches the totals | integration | `test_analysis_pipeline.py` |
| Date column | name-based choice, Arabic names, override, invalid override is 422 | unit + integration | `test_ml_units.py`, `test_analysis_pipeline.py` |
| Failure isolation | a module raising becomes a warning, the others finish; all failing fails the run; unreachable queue and unreadable file fail the run, not the worker | integration | `test_analysis_pipeline.py` |
| Re-run | replaces the version's insights (no duplicates), inherits earlier settings, a second run while one is active is 409, `?version=N` replaces only that version | integration | `test_analysis_pipeline.py` |
| Chain | profile ready queues the analysis only when `AUTO_ANALYZE` is true; enqueue never raises | integration | `test_analysis_pipeline.py` |
| Stuck runs | running runs older than `INGEST_STUCK_MINUTES` fail, recent and finished ones stay | integration | `test_analysis_pipeline.py` |
| Duplicates | 150 planted duplicates are named in every insight and cap confidence at medium | integration | `test_analysis_caveats.py` |
| Null-heavy columns | blanked revenue/customer/date cells are excluded, counted exactly, warned, confidence capped, totals equal the expected figure | integration | `test_analysis_caveats.py` |
| Quality score | below 60 caps at medium, below 40 at low; clean data has no caveats; caps never raise | integration + unit | `test_analysis_caveats.py` |
| Refunds | planted refunds: net/gross/returns count and total exact; Pareto, trend and cohort use the documented basis | integration | `test_analysis_caveats.py` |
| Formatting and impact | locale separators, Arabic digits, impact formula and weights, period helpers | unit | `test_ml_units.py` |
| Isolation | every new route returns 404 for another tenant and is in the route-coverage test | integration | `test_isolation.py` |
| Migration | `0006` creates and drops `analysis_runs` and `insights` | integration | `test_migration.py` |
| Coverage | `app/ml` >= 85% and `app/services/analysis.py` >= 85% in `scripts/check_coverage.py` | gate | `make test` |
