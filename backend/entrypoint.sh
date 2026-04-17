#!/usr/bin/env bash
# Railway/Docker entrypoint: apply any pending Alembic migrations, then boot
# uvicorn. Keeps schema in lockstep with the deployed code.
set -euo pipefail

PORT="${PORT:-8000}"

if [[ "${RUN_MIGRATIONS:-1}" == "1" ]]; then
  echo "[entrypoint] Running alembic upgrade head…"
  alembic upgrade head
else
  echo "[entrypoint] RUN_MIGRATIONS=0 — skipping alembic."
fi

echo "[entrypoint] Starting uvicorn on :${PORT}"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers 1 \
  --timeout-keep-alive 120
