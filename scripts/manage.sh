#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
GAME_SERVER_PORT="${GAME_SERVER_PORT:-8100}"
UVICORN_OPTS="${UVICORN_OPTS:-}"

start_dashboard() {
  echo "[dashboard] Starting app.main on port ${DASHBOARD_PORT}"
  cd "$PROJECT_ROOT"
  exec uvicorn app.main:app --host 0.0.0.0 --port "$DASHBOARD_PORT" $UVICORN_OPTS
}

start_server() {
  echo "[server] Starting server.network on port ${GAME_SERVER_PORT}"
  cd "$PROJECT_ROOT"
  exec uvicorn server.network:create_app --factory --host 0.0.0.0 --port "$GAME_SERVER_PORT" $UVICORN_OPTS
}

run_dual() {
  cd "$PROJECT_ROOT"
  uvicorn app.main:app --host 0.0.0.0 --port "$DASHBOARD_PORT" $UVICORN_OPTS &
  DASH_PID=$!
  uvicorn server.network:create_app --factory --host 0.0.0.0 --port "$GAME_SERVER_PORT" $UVICORN_OPTS &
  SERVER_PID=$!
  trap 'echo "Stopping services"; kill $DASH_PID $SERVER_PID 2>/dev/null || true' INT TERM
  wait $DASH_PID $SERVER_PID
}

cat <<MENU
Nigh.ty workflow helper
-----------------------
1) Start dashboard + gameplay server
2) Start gameplay server only
3) Start dashboard only
MENU

read -rp "Select option: " choice
case "$choice" in
  1)
    run_dual
    ;;
  2)
    start_server
    ;;
  3)
    start_dashboard
    ;;
  *)
    echo "Unknown option" >&2
    exit 1
    ;;
esac
