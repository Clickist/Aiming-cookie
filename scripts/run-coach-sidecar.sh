#!/usr/bin/env bash
# Start the Pi coach-runtime HTTP sidecar (loopback only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_SOURCE_DIR="${PI_SOURCE_DIR:-$REPO_ROOT/third_party/pi}"
TSX_LOADER="$PI_SOURCE_DIR/node_modules/tsx/dist/loader.mjs"
SIDECAR_ENTRY="$REPO_ROOT/webapp/coach-runtime/start-sidecar.ts"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--help]

Starts the coach sidecar on 127.0.0.1 (default port COACH_SIDECAR_PORT=8765).

Environment:
  PI_SOURCE_DIR       Pi source checkout (default: third_party/pi)
  COACH_SIDECAR_PORT  Listen port (default: 8765)
  COACH_SIDECAR_HOST  Bind host (default: 127.0.0.1)

Requires Node >= 22 and npm install in PI_SOURCE_DIR (for tsx).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -d "$PI_SOURCE_DIR" ]]; then
  echo "error: PI_SOURCE_DIR is not a directory: $PI_SOURCE_DIR" >&2
  echo "hint: clone or sync third_party/pi and set PI_SOURCE_DIR if needed." >&2
  exit 1
fi

if [[ ! -f "$TSX_LOADER" ]]; then
  echo "error: tsx loader not found at $TSX_LOADER" >&2
  echo "hint: cd \"$PI_SOURCE_DIR\" && npm install" >&2
  exit 1
fi

if [[ ! -f "$SIDECAR_ENTRY" ]]; then
  echo "error: sidecar entry missing: $SIDECAR_ENTRY" >&2
  exit 1
fi

export PI_SOURCE_DIR
exec node --import "$TSX_LOADER" "$SIDECAR_ENTRY"