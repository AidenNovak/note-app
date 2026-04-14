.PHONY: install dev backend-install backend-dev backend-lint backend-test backend-migrate-storage native-install native-dev

BACKEND_PORT ?= 8000

install: backend-install native-install

dev:
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

native-install:
	cd easystarter && corepack enable && pnpm install

native-dev:
	cd easystarter && pnpm dev:native
