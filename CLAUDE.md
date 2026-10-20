# AI Business Analyst Platform

## Mission
Multi-tenant web platform. A company uploads business data (CSV/XLSX), gets an
automated senior-analyst report built from ML models, and chats with an AI agent
that gives evidence-backed business decisions.

## Golden rules
1. The LLM never computes numbers. Every figure comes from executed SQL, Python or
ML code and is returned with evidence (query, result table, chart spec).
2. Every DB row and file is scoped by tenant_id. Take tenant_id from the JWT only,
never from request bodies or query strings.
3. Prefer "not enough data" over guessing. Every insight carries a confidence level
(high/medium/low) and the sample size behind it.
4. No step is done with failing tests, lint errors or type errors.
5. Secrets live only in .env. .env.example documents every variable.
6. The LLM provider sits behind an LLMClient interface in backend/app/llm/. No
vendor SDK is imported anywhere else.

## Stack
Backend: Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16,
Redis, Celery + beat, DuckDB, pandas, scikit-learn, LightGBM, XGBoost, SHAP,
statsforecast, MLflow, structlog, pytest, ruff, mypy.
Frontend: Next.js (App Router), TypeScript strict, Tailwind, shadcn/ui,
TanStack Query, Recharts.
Infra: Docker Compose (api, worker, beat, redis, postgres, mlflow, sandbox,
frontend), GitHub Actions.

## Repo layout
```
backend/app/{api,core,db,models,schemas,services,ml,agent,llm,reports,workers}
backend/tests
frontend/
evaluation/ (datasets, questions.yaml, runners)
docs/
docker-compose.yml, Makefile, .env.example
```

## Commands
```
make up | make down | make test | make lint | make migrate | make seed
```

## Conventions
- API prefix /api/v1. Errors are JSON: {code, message, details}.
- Type hints everywhere, docstrings on public functions, small focused functions.
- Conventional commits (feat:, fix:, test:, docs:, chore:).
- One git branch per step, named step-NN-short-name.
- New ML modules return a typed result object with: value, confidence, sample_size,
method, warnings.

## Core tables
tenants, users, datasets, dataset_versions, column_profiles, analysis_runs,
insights, model_runs, conversations, messages, reports.

## Progress log (append one line after each finished step)
- [x] Step 0 bootstrap — monorepo skeleton: FastAPI health, Celery ping, compose stack, Makefile, CI, pre-commit
- [x] Step 1 backend core and auth — tenant-isolated auth (argon2, JWT + rotating refresh cookie), team roles, Redis rate limiting, error envelope, Alembic schema, refresh tokens scoped by tenant, 64 tests with per-package coverage gate
- [x] Step 2 datasets — CSV/TSV/XLSX upload to tenant-scoped storage, streaming Parquet ingestion via Celery (all-strings fallback), versions, soft delete, and a locked-down SafeQueryService (sqlglot + DuckDB lockdown, 60+ abuse tests), 293 tests
- [x] Step 3 profiling and cleaning — separate profile stage (DuckDB, memory-limited) with column stats, EN/AR role rules + LLMClient/FakeLLMClient refinement, PII masking, 0-100 quality score, non-destructive cleaning plan that creates a new version, identifier columns kept as text, 549+ tests with a profiling coverage gate
- [x] Step 4 analytics pipeline — AnalysisModule framework + registry + capability detection, KPI / trend+STL seasonality / Pareto+dead stock / cohort retention modules on a hardened per-run DuckDB table, impact score, data-quality caveats, net-vs-refund rules, auto-run after profiling, /analyze, runs and insights endpoints, ground-truth dataset recovered exactly, 600+ tests with app/ml coverage gate
- [x] Step 5 forecasting — ForecastModule (total + top 5 categories) with rolling-origin backtests of seasonal naive/ETS/ARIMA/Theta/LightGBM, MASE winner that must beat the baseline, 80/95% ranges, honest low-confidence wording, partial-period drop, time/series/memory budgets, per-tenant MLflow runs, async POST /datasets/{id}/forecast via Celery (model_runs), --years 3 generator, forecasting coverage gate
- [ ] Step 6 anomalies and segmentation
- [ ] Step 7 churn, drivers, what-if
- [ ] Step 8 agent and chat backend
- [ ] Step 9 evaluation suite
- [ ] Step 10 reports and alerts
- [ ] Step 11 frontend foundation
- [ ] Step 12 dashboard and insights
- [ ] Step 13 chat, reports, settings UI
- [ ] Step 14 security and observability
- [ ] Step 15 deployment and docs

## Environment notes
- Windows 11 with PowerShell. Docker Desktop with WSL2 has about 7.6 GB RAM, so
  keep containers light.
- The project path contains spaces: `D:\Learning\AI Projects\AI Data Analyst`.
  Always quote paths, and use relative paths inside scripts, the Makefile and CI.
- GNU Make 3.81 (GnuWin32). Keep the Makefile simple: one-line recipes that work
  with cmd.exe, no bash-only syntax. Targets must call `docker compose` and `uv`.
- `.gitattributes` has `* text=auto eol=lf` so shell scripts keep LF endings in Docker.
- Git has a remote `origin` and a `main` branch. Branch per step; never push, the
  user pushes.

## Workflow rules (every step)
1. Do all the work yourself, including what you would normally ask the user to run in PowerShell: start the stack (`make up`, `make migrate`), run the manual walkthrough against it (upload the sample files, call the endpoints, read the results) and show the real outputs, trimmed to what matters, with a pass or fail per check. Do not hand over lists of commands.
2. Leave the machine tidy: run `make down` at the end (this also stops containers started with `make db-up`), leave no background processes, and keep temp files only under the system temp folder.
3. Secrets: never print, log or commit tokens, passwords or `.env` contents. Use throwaway demo users.
4. When `make lint` and `make test` are green and the work is committed: `git push -u origin <branch>`; `gh pr create` (title = the commit title; body = what was built, test and coverage numbers, how it was verified, known limits, decisions for the user); then watch the checks to the end with `gh pr checks --watch`.
5. If CI fails: read the failing log (`gh run view --log-failed`), find the cause, fix it with a NEW commit (never amend), push, and watch again. Maximum 3 attempts, then stop and report. When the cause is a difference between the user's Windows machine and the Linux CI runner, say so.
6. Do NOT merge by yourself. Stop once CI is green and report: PR link, CI result and run time, test and coverage numbers, the manual check table, decisions for the user.
7. When the user says "merge": run `gh pr merge --merge --delete-branch`, then `git switch main`, `git pull origin main`, `git branch -D <the merged step branch only>`, `git fetch --prune`, `git status`, `git log --oneline -3`, then watch the CI run on main to the end and report.
8. Never force-push, rewrite history, push directly to main, change repository or branch settings, or delete anything other than the merged step branch.
9. Stop and ask when something needs a judgement call: security, deleting data, costs, or a change to the plan.
