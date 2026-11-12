#!/usr/bin/env bash
# G23 P0 — 双进程误复位复现驱动器（两个真实 Python 进程）
# 用法: bash scripts/g23/run_repro.sh <venv_python> [结果输出目录]
set -euo pipefail

PY="${1:?用法: run_repro.sh <venv_python> [outdir]}"
OUT="${2:-$(mktemp -d /tmp/g23-repro-XXXXXX)}"
ROOT="$OUT/root"
mkdir -p "$ROOT/data"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() { [[ -f "$ROOT/a.pid" ]] && kill -9 "$(cat "$ROOT/a.pid")" 2>/dev/null || true; }
trap cleanup EXIT

echo "== G23 双进程复现 root=$ROOT ==" | tee "$OUT/driver.log"
G23_ROOT="$ROOT" "$PY" "$SCRIPT_DIR/repro_false_reset_a.py" >"$OUT/a.log" 2>&1 &
A_PID=$!
echo "$A_PID" > "$ROOT/a.pid"

# 等 A 建好批次并进入提交
for _ in $(seq 1 600); do [[ -f "$ROOT/ready.flag" ]] && break; sleep 0.1; done
if [[ ! -f "$ROOT/ready.flag" ]]; then
  echo "FAIL: A 未在 60s 内就绪" | tee -a "$OUT/driver.log"; cat "$OUT/a.log"; exit 1
fi

G23_ROOT="$ROOT" "$PY" "$SCRIPT_DIR/repro_false_reset_b.py" >"$OUT/b.log" 2>&1
B_RC=$?
wait "$A_PID" || true
echo "== B 输出 ==" | tee -a "$OUT/driver.log"
cat "$OUT/b.log" | tee -a "$OUT/driver.log"
echo "== A 输出 ==" | tee -a "$OUT/driver.log"
cat "$OUT/a.log" | tee -a "$OUT/driver.log"
exit $B_RC
