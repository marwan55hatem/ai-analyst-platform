COV_MIN ?= 85

.PHONY: up down logs test lint migrate seed db-up check-storage

up:
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

db-up:
	docker compose up -d --wait postgres redis

test:
	uv run --directory backend pytest --cov=app --cov-report=term-missing --cov-report=json
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
