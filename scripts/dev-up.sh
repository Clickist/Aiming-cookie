#!/usr/bin/env bash
# Dev: coach sidecar (background) + FastAPI API (foreground). Ctrl+C stops both.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--help]

Starts the Pi coach sidecar in the background, then runs the API in the foreground.
On exit (Ctrl+C), the sidecar process is stopped.

Environment:
  API_HOST / API_PORT     Uvicorn bind (default 127.0.0.1:8000)
  COACH_SIDECAR_PORT      Sidecar port (default 8765, via run-coach-sidecar.sh)
  PI_SOURCE_DIR           Pi checkout for tsx (default: third_party/pi)

In other terminals:
  python -m webapp.backend.worker
  cd webapp/frontend && npm run dev
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SIDECAR_SCRIPT="$REPO_ROOT/scripts/run-coach-sidecar.sh"
if [[ ! -x "$SIDECAR_SCRIPT" ]]; then
  echo "error: missing or non-executable: $SIDECAR_SCRIPT" >&2
  exit 1
fi

SIDECAR_PID=""
cleanup() {
  if [[ -n "${SIDECAR_PID}" ]] && kill -0 "${SIDECAR_PID}" 2>/dev/null; then
    kill "${SIDECAR_PID}" 2>/dev/null || true
    wait "${SIDECAR_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "==> starting coach sidecar (background)…"
"$SIDECAR_SCRIPT" &
SIDECAR_PID=$!

echo "==> sidecar pid ${SIDECAR_PID}"
echo "==> starting API at http://${API_HOST}:${API_PORT}"
echo ""
echo "Other terminals:"
echo "  cd \"$REPO_ROOT\" && source .venv/bin/activate && python -m webapp.backend.worker"
echo "  cd \"$REPO_ROOT/webapp/frontend\" && npm run dev"
echo ""

cd "$REPO_ROOT"
uvicorn webapp.backend.app:app --reload --host "$API_HOST" --port "$API_PORT"