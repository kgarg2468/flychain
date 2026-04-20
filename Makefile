.PHONY: help install dev down logs lint typecheck test fmt clean

help:
	@echo "FlyChain dev commands"
	@echo
	@echo "  make install     install JS + Python deps (pnpm + uv)"
	@echo "  make dev         docker compose up -d"
	@echo "  make down        docker compose down"
	@echo "  make logs        tail docker compose logs"
	@echo "  make lint        lint JS + Python"
	@echo "  make typecheck   typecheck JS"
	@echo "  make test        run unit tests (JS + Python)"
	@echo "  make fmt         format JS + Python"
	@echo "  make clean       remove build artifacts"

install:
	pnpm install
	uv sync

dev:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

lint:
	pnpm -r --if-present lint
	uv run ruff check .

typecheck:
	pnpm -r --if-present typecheck

test:
	pnpm -r --if-present test
	uv run pytest

fmt:
	pnpm run format
	uv run ruff format .

clean:
	rm -rf node_modules .turbo .next dist
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.egg-info" -type d -prune -exec rm -rf {} +
	rm -rf .venv .ruff_cache .pytest_cache .mypy_cache
