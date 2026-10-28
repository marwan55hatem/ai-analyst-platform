COV_MIN ?= 85
TEST_WORKERS ?= 1

.PHONY: up down logs test lint migrate seed db-up check-storage frontend-install frontend-lint frontend-typecheck frontend-test frontend-build frontend-e2e api-client

up:
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

db-up:
	docker compose up -d --wait postgres redis

test:
	uv run --directory backend python scripts/run_tests.py
	uv run --directory backend python scripts/check_coverage.py --min $(COV_MIN)

lint:
	uv run --directory backend ruff check .
	uv run --directory backend ruff format --check .
	uv run --directory backend mypy app

migrate:
	docker compose exec -T api alembic upgrade head

seed:
	docker compose exec -T api python -m app.db.seed

# Proves api and worker share STORAGE_DIR: api writes a file, worker reads it.
check-storage:
	docker compose exec -T api sh -c "echo shared-ok > /app/storage/.probe"
	docker compose exec -T worker sh -c "cat /app/storage/.probe && rm /app/storage/.probe"

# Frontend (Next.js in ./frontend). Day-to-day development: npm --prefix frontend run dev
frontend-install:
	npm --prefix frontend ci

frontend-lint:
	npm --prefix frontend run lint

frontend-typecheck:
	npm --prefix frontend run typecheck

frontend-test:
	npm --prefix frontend run test

frontend-build:
	npm --prefix frontend run build

# Playwright end-to-end tests against the running stack (make up, make migrate first)
frontend-e2e:
	npm --prefix frontend run e2e

# Regenerate the typed API client from the FastAPI schema (no server needed)
api-client:
	uv run --directory backend python scripts/dump_openapi.py ../frontend/src/lib/api/openapi.json
	npm --prefix frontend run api:generate
