.PHONY: up down logs test lint migrate seed

up:
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	uv run --directory backend pytest --cov=app

lint:
	uv run --directory backend ruff check .
	uv run --directory backend ruff format --check .
	uv run --directory backend mypy app

migrate:
	docker compose exec -T api alembic upgrade head

seed:
	docker compose exec -T api python -m app.db.seed
