#!/usr/bin/env bash
# WiFi-Doctor local runner for Linux / macOS.
#   ./scripts/run_local.sh          -> backend + frontend dev server
#   sudo ./scripts/run_local.sh     -> same, with raw-socket (scapy) probing
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"

echo "==> WiFi-Doctor | root: $ROOT"

# ---------- backend ----------
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "==> creating Python virtualenv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "==> starting backend on http://127.0.0.1:8000"
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cleanup() {
  echo; echo "==> shutting down"
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ---------- frontend ----------
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "==> installing frontend dependencies"
  npm install --no-fund --no-audit
fi
echo "==> dashboard: http://localhost:5173"
npm run dev
