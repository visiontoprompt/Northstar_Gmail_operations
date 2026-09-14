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
backend_ready=false
frontend_ready=false

port_in_use() {
  ss -ltnH 2>/dev/null | awk -v port=":$1" '$4 ~ port "$" { found = 1 } END { exit !found }'
}

backend_is_ours() {
  curl -fsS -m 2 http://127.0.0.1:8000/api/v1/health 2>/dev/null | rg -q '"status":"ok"'
}

frontend_is_ours() {
  curl -fsS -m 2 http://127.0.0.1:5173/ 2>/dev/null | rg -q 'Account registry'
}

cleanup() {
  trap - INT TERM EXIT
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && wait "$frontend_pid" 2>/dev/null || true
  [[ -n "$backend_pid" ]] && wait "$backend_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

if port_in_use 8000; then
  if backend_is_ours; then
    backend_ready=true
    echo "Backend already running on http://localhost:8000/"
  else
    echo "Port 8000 is already in use by another service. Stop it, then run ./dev.sh again." >&2
    exit 1
  fi
fi

if port_in_use 5173; then
  if frontend_is_ours; then
    frontend_ready=true
    echo "Frontend already running on http://localhost:5173/"
  else
    echo "Port 5173 is already in use by another service. Stop it, then run ./dev.sh again." >&2
    exit 1
  fi
fi

if [[ "$backend_ready" != true ]]; then
  (
    cd "$BACKEND_DIR"
    # Local development always opens the dedicated account Chrome window.
    # Production workers must supply these settings through their deployment.
    export BROWSER_LAUNCH_ENABLED=true
    # Keep authentication in a standard Chrome surface. Observation/CDP is
    # opt-in for a later post-login diagnostic workflow.
    export BROWSER_OBSERVER_ENABLED=false
    exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
  ) &
  backend_pid=$!
fi

if [[ "$frontend_ready" != true ]]; then
  (
    cd "$FRONTEND_DIR"
    exec npm run dev -- --host 0.0.0.0
  ) &
  frontend_pid=$!
fi

echo "Frontend: http://localhost:5173/"
echo "Backend:  http://localhost:8000/"
echo "Press Ctrl+C to stop both services."

if [[ -n "$backend_pid" && -n "$frontend_pid" ]]; then
  wait -n "$backend_pid" "$frontend_pid"
elif [[ -n "$backend_pid" ]]; then
  wait "$backend_pid"
elif [[ -n "$frontend_pid" ]]; then
  wait "$frontend_pid"
else
  echo "Both services are already running."
  trap - INT TERM EXIT
fi
