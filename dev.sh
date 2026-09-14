#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

if [[ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]]; then
  echo "Backend environment is missing: $BACKEND_DIR/.venv/bin/uvicorn" >&2
  echo "Install it once with: cd backend && python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]]; then
  echo "Frontend dependencies are missing." >&2
  echo "Install them once with: cd frontend && npm install" >&2
  exit 1
fi

backend_pid=""
frontend_pid=""

cleanup() {
  trap - INT TERM EXIT
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && wait "$frontend_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

(
  cd "$BACKEND_DIR"
  exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
) &
backend_pid=$!

(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --host 0.0.0.0
) &
frontend_pid=$!

echo "Gmail Operations SaaS is starting..."
echo "Frontend: http://localhost:5173/"
echo "Backend:  http://localhost:8000/"
echo "Press Ctrl+C to stop both services."

wait -n "$backend_pid" "$frontend_pid"
