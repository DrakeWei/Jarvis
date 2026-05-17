#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
COMPOSE_FILE="${BACKEND_DIR}/docker-compose.postgres-test.yml"
PYTHON_BIN="${PYTHON_BIN:-python3}"
POSTGRES_URL="${JARVIS_TEST_POSTGRES_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres}"

cleanup() {
  docker compose -f "${COMPOSE_FILE}" down -v >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker compose -f "${COMPOSE_FILE}" up -d

POSTGRES_READY=0
for _ in $(seq 1 30); do
  if JARVIS_TEST_POSTGRES_URL="${POSTGRES_URL}" "${PYTHON_BIN}" - <<'PY'
import os
import psycopg

url = os.environ["JARVIS_TEST_POSTGRES_URL"].replace("+psycopg", "")
with psycopg.connect(url, connect_timeout=2) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
PY
  then
    POSTGRES_READY=1
    break
  fi
  sleep 1
done

if [[ "${POSTGRES_READY}" != "1" ]]; then
  echo "Postgres test container did not become ready in time." >&2
  exit 1
fi

cd "${ROOT_DIR}"
PYTHONPATH="${BACKEND_DIR}" JARVIS_TEST_POSTGRES_URL="${POSTGRES_URL}" \
  "${PYTHON_BIN}" -m pytest backend/tests/test_postgres_concurrency.py "$@"
