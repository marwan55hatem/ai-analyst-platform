# Step 0 architecture review

- **Layout**: one backend package (`app`) split by layer; `api -> services -> (db, ml, llm)`; `agent` and `reports` call `services`/`ml` only. Vendor SDKs stay inside `llm/`.
- **Processes**: one backend image runs as api, worker and beat (different commands). Postgres holds state, Redis is broker/result store, MLflow tracks models (sqlite on a volume to stay light).
- **Tenancy**: tenant_id comes from the JWT only (Step 1); every table and file path carries it.
- **Risks**: 7.6 GB Docker RAM (mem limits set, worker concurrency 1); Windows paths with spaces (relative paths only); sandbox/frontend services are deferred to later steps.
