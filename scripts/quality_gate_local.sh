#!/usr/bin/env bash
# 本地一键质量门禁（任务书 10 线 ADR-0159；本仓无 CI，本地即门禁）。
#
# 用法（仓库根目录）：
#   bash scripts/quality_gate_local.sh              # 全量四步
#   SKIP_BROWSER=1 bash scripts/quality_gate_local.sh   # 跳过头照 golden 步（其余照跑）
#
# 步骤：
#   1. cartography lane 独立覆盖率闸（scope=app/lib/cartography，下限
#      CARTO_COV_FLOOR，缺省 50 → ratchet 到 60 后收口；与后端 75% 分开计）
#   2. 头照场景 golden 像素级校验（pr-blocking 集合硬失败；单场景串行）
#   3. ratchet 劣化闸（劣化超容差 → 拦截；waiver 未到期的放行并高亮）
#   4. 趋势报告刷新（看板数值段 + 控制台趋势表）
#
# 纪律：任何一步失败即非 0 退出（P8 验收要求全量一次跑通并附完整输出）。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python"
  elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  else
    PY="python"
  fi
fi

export JWT_SECRET_KEY="${JWT_SECRET_KEY:-test-secret-migration-32-chars-okay}"
export USE_REDIS="${USE_REDIS:-false}"
export CARTO_COV_FLOOR="${CARTO_COV_FLOOR:-50}"

step() { echo; echo "======================================================================"; echo "[gate] $1"; echo "======================================================================"; }

FAILED=0

step "1/4 cartography lane 独立覆盖率闸（floor=${CARTO_COV_FLOOR}）"
# lane 测试面会对默认 dev 库做建表/清理（repo 既有行为）——gate 的 lane 跑
# 用一次性临时库隔离，保护 step 3 ratchet 依赖的事实库数据。
_LANE_DB_DIR="$(mktemp -d)"
DATABASE_URL="sqlite:///${_LANE_DB_DIR}/lane.db" "$PY" scripts/coverage_cartography_gate.py || FAILED=1
rm -rf "${_LANE_DB_DIR}"

if [ "${SKIP_BROWSER:-0}" != "1" ]; then
  step "2/4 头照场景 golden 像素校验（pr-blocking 硬门禁，单场景串行）"
  "$PY" scripts/golden_baseline.py verify || FAILED=1
else
  step "2/4 头照 golden 校验：SKIP_BROWSER=1 → 跳过"
fi

step "3/4 ratchet 劣化闸"
"$PY" scripts/quality_ratchet_gate.py check || FAILED=1

step "4/4 趋势报告刷新"
"$PY" scripts/quality_trend_report.py --last 10 \
  --csv docs/dev/ac-10-quality-trend.csv \
  --dashboard docs/dev/ac-10-quality-dashboard.md || FAILED=1

echo
echo "======================================================================"
if [ "$FAILED" -eq 0 ]; then
  echo "[gate] ✅ 全部步骤通过（quality_gate_local.sh）"
else
  echo "[gate] ❌ 存在失败步骤（见上文）"
fi
echo "======================================================================"
exit "$FAILED"
