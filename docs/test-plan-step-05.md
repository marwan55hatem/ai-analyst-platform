# Test plan – Step 5 forecasting

Pyramid: many fast unit tests on the pure forecasting code, a few integration tests through the
real API/Celery (eager) with Postgres and DuckDB, and an end-to-end Docker run done by hand.
Coverage target: `app/ml/forecasting` >= 85% (gate in `backend/scripts/check_coverage.py`).

| Area | Type | Cases |
|---|---|---|
| Series preparation (`series.py`) | unit | incomplete trailing month/week/quarter dropped + warning; complete period kept; gaps filled with zero + warning; outlier capped for training only, raw kept; seasonal peak NOT capped; frequency choice rules; too short -> None |
| Metrics (`metrics.py`) | unit | sMAPE/MASE/MAE against hand-computed values; zero/zero periods; constant history (MASE undefined) |
| Fold planning + selection (`engine.py`) | unit | folds scale with history; too short -> no backtest; winner by MASE; baseline wins -> `no_better_than_baseline`, low confidence; model failure is skipped, not fatal |
| Accuracy (acceptance) | integration | synthetic trend+season+noise: winner sMAPE < 12% and beats seasonal naive; pooled 80% interval coverage 70-90% |
| Edge series | unit | < 2 cycles -> simple models + low; series with gaps; constant series; tiny series; all run without error |
| Confidence (`confidence.py`) | unit | one case per rule: high, medium, low, coverage downgrade, no-better, no-backtest |
| Wording (`text.py`) | unit | high / medium / low / no-better: title and summary say "estimate", show the 80% range, low and no-better open with "This forecast is unreliable" and the reason |
| Budget + memory guard | unit | injected RSS above `FORECAST_MEMORY_MB` -> LightGBM skipped, groups dropped, warnings, forecast still returned; time limit with a fake clock |
| Determinism | unit | same seed -> identical forecast |
| MLflow (`tracking.py`) | integration | temp local store: experiment `tenant-<id>`, tags, params, per-model metrics, `forecast.json`, no raw rows; unreachable server -> warning, forecast succeeds, fast |
| Pipeline | integration | forecast module in the auto run; evidence SQL passes `validate_sql` and replays through `SafeQueryService`; Step 4 module set + facts unchanged |
| API | integration | `GET /analysis-runs/{id}/forecast`; `POST /datasets/{id}/forecast` -> 202 and polling; 422 for bad metric / group_by / horizon / too many groups; 409 while running; any role may call; `tenant_id` in body ignored/rejected |
| Isolation | integration | tenant B gets 404 on every new route (added to `test_isolation.py`) |
| Generator | unit | default output byte-identical to Step 4 (facts); `years=3` plants a trend and a held-out year; daily variant |
| Docker | manual | images build; api/worker/beat healthy; `docker stats` during a forecast; MLflow shows runs |

Gaps knowingly left: no holiday/Ramadan/Eid or promotion effects (documented), no external data,
no load test beyond the memory measurement.
