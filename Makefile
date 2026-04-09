.PHONY: install dev dev-native backend-install backend-dev backend-lint backend-test backend-migrate-storage web-install web-dev web-lint web-build native-install native-dev

BACKEND_PORT ?= 8000

install: backend-install web-install

dev:
	$(MAKE) -j2 backend-dev web-dev

dev-native:
	$(MAKE) -j2 backend-dev native-dev

backend-install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt

backend-dev:
	cd backend && set -a && [ -f .env ] && . ./.env || true && set +a && . .venv/bin/activate && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT)

backend-lint:
	cd backend && . .venv/bin/activate && ruff check .

backend-test:
	cd backend && . .venv/bin/activate && pytest --ignore=tests/test_e2e.py

backend-migrate-storage:
	cd backend && set -a && [ -f .env ] && . ./.env || true && set +a && . .venv/bin/activate && python scripts/migrate_legacy_files_to_r2.py

web-install:
	cd easystarter && corepack enable && pnpm install

web-dev:
	cd easystarter && corepack enable && pnpm dev:web+server

web-lint:
	cd easystarter/apps/web && corepack enable && pnpm lint

web-build:
	cd easystarter/apps/web && corepack enable && pnpm build

native-install:
	cd easystarter && corepack enable && pnpm install

native-dev:
	cd easystarter && corepack enable && pnpm dev:native+server
