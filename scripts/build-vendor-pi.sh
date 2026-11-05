#!/usr/bin/env bash
# Build the vendored Pi RPC runtime (goal-pi-native-host-vnext / ADR-0285).
#
# Produces the artifacts the backend Pi bridge spawns:
#   vendor/pi/packages/coding-agent/dist/bundle/rpc-entry.js  (self-contained, primary)
#   vendor/pi/packages/coding-agent/dist/rpc-entry.js         (workspace build, fallback)
#
# Idempotent: skips npm install when node_modules is already present unless
# --reinstall is given. Needs node >=22.19 (engines field of the submodule) and
# npm. Run from the repo root or anywhere — paths resolve from this script.
#
# Usage:
#   scripts/build-vendor-pi.sh              # install-if-needed + build
#   scripts/build-vendor-pi.sh --reinstall  # wipe node_modules and reinstall
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_PI="$REPO_ROOT/vendor/pi"
BUNDLE_ENTRY="$VENDOR_PI/packages/coding-agent/dist/bundle/rpc-entry.js"
DIST_ENTRY="$VENDOR_PI/packages/coding-agent/dist/rpc-entry.js"

REINSTALL=0
[[ "${1:-}" == "--reinstall" ]] && REINSTALL=1

if [[ ! -e "$VENDOR_PI/.git" ]]; then
  echo "error: $VENDOR_PI is not an initialized git submodule" >&2
  echo "  run: git submodule update --init vendor/pi" >&2
  exit 2
fi

command -v node >/dev/null 2>&1 || { echo "error: node not found on PATH (need >=22.19)" >&2; exit 2; }
command -v npm >/dev/null 2>&1 || { echo "error: npm not found on PATH" >&2; exit 2; }

cd "$VENDOR_PI"

if [[ ! -f package.json ]]; then
  echo "error: $VENDOR_PI/package.json missing — wrong submodule content" >&2
  exit 2
fi

PIN="$(git rev-parse --short HEAD)"
echo "[build-vendor-pi] submodule HEAD: $(git rev-parse HEAD)"

if [[ "$REINSTALL" == "1" ]]; then
  echo "[build-vendor-pi] --reinstall: removing node_modules"
  rm -rf node_modules
fi

if [[ ! -d node_modules ]]; then
  echo "[build-vendor-pi] npm install (first run; cached afterwards)"
  npm install --no-audit --no-fund
else
  echo "[build-vendor-pi] node_modules present — skipping install (use --reinstall to force)"
fi

echo "[build-vendor-pi] npm run build (tui→ai→…→coding-agent chain)"
npm run build

ok=0
[[ -f "$BUNDLE_ENTRY" ]] && echo "[build-vendor-pi] OK bundle: $BUNDLE_ENTRY" || ok=1
[[ -f "$DIST_ENTRY" ]] && echo "[build-vendor-pi] OK dist:   $DIST_ENTRY" || true
if [[ "$ok" != "0" ]]; then
  echo "error: bundle rpc-entry.js missing after build" >&2
  exit 1
fi

echo "[build-vendor-pi] done (pin $PIN). Verify with: python scripts/pi_doctor.py"
