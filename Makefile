COV_MIN ?= 85

.PHONY: up down logs test lint migrate seed db-up

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
