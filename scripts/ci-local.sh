#!/usr/bin/env bash
# ci-local.sh — 本地一条命令跑出与 CI PR lane 完全相同的门禁（#671）。
#
# 动机：本地只 lint 改动文件、CI 跑仓级门禁，导致两次推送后 CI 红
# （98326c9 / 7699b09 都是这类 post-hoc 修复）。此脚本的每条命令与
# .github/workflows/production.yml 的阻塞 lane 逐字对齐，对齐关系由
# tests/test_ci_local_gate_contract.py 契约断言 —— workflow 改动而脚本
# 不跟会在测试里红，而不是在推送后红。
#
# 用法：
#   scripts/ci-local.sh          # 全量：lint + 类型 + 前端测试/构建 + 后端/perf/cartography pytest
#   scripts/ci-local.sh --fast   # 只跑快速门禁：ruff + eslint + typecheck + vitest
#
# 不在本地复现的 lane（需要 service containers / docker，见 workflow）：
#   db-migrations、real-services-smoke、deploy-config、runtime-validator。
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  "") FAST=0 ;;
  --fast) FAST=1 ;;
  *) echo "usage: $0 [--fast]" >&2; exit 2 ;;
esac

# 把项目虚拟环境放到 PATH 最前：下面的命令与 CI 字面一致（ruff/pytest 直名调用），
# 本地解析到 .venv/bin 里的版本；CI 里是 pip install 的全局命令。
[ -d .venv/bin ] && export PATH="$PWD/.venv/bin:$PATH"

# 与 CI 的 Set Environment Variables 对齐（不覆盖已有的本地配置）。
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-ci-test-secret-key-32-chars-ok}"
export ENV="${ENV:-development}"
export LLM_API_KEY="${LLM_API_KEY:-test-key-not-real}"

step() { printf '\n=== %s ===\n' "$1"; }

step "ruff (repo-wide, lint job)"
ruff check --output-format=github app/ tests/ main.py manage.py

step "eslint (repo-wide, lint job)"
(cd frontend && npx eslint . --max-warnings 0)

step "typecheck (test-frontend job)"
(cd frontend && npm run typecheck)

step "vitest (test-frontend job)"
(cd frontend && npm run test:ci)

# ── 契约层（#700 流程硬化）：根目录的跨模块契约/stub 测试不属任何 CI PR
# lane 单独清单，历史上两次事故（#678 域词汇契约、#694 FrozenCatalog stub）
# 都因 --fast 不碰后端 pytest 而漏过。此层是**精选清单**（秒级），不是全量
# 后端 lane 的替代——动 registry/catalog/engine 面仍须跑全量 lane。
step "contract tier (root-level cross-module contracts)"
pytest \
  tests/test_tool_meta_contract.py \
  tests/test_subagent_context_isolation_436.py \
  tests/test_ci_local_gate_contract.py \
  tests/test_ci_perf_coverage_contract.py \
  --no-cov -q

if [ "$FAST" = "1" ]; then
  echo "--fast：跳过 next build 与后端/perf/cartography pytest lanes"
  exit 0
fi

step "next build (test-frontend job)"
(cd frontend && npm run build)

step "backend tests (test-backend job)"
pytest --cov=app --cov-report=term-missing --cov-fail-under=75 \
  --timeout=60 --timeout-method=thread \
  -m "not perf and not cartography and not real_services" -q

step "perf harness (test-perf job)"
pytest tests/benchmarks/test_perf_harness.py tests/benchmarks/test_transport_perf.py \
  tests/benchmarks/test_job_runtime_perf.py tests/benchmarks/test_provenance_perf.py \
  tests/benchmarks/test_llm_http_pooling_perf.py \
  tests/benchmarks/test_perf_mapspec_mutation_cost.py tests/benchmarks/test_dispatch_stall_perf.py \
  tests/benchmarks/test_perf_large_workspace.py \
  -m perf --no-cov --timeout=180 --timeout-method=thread -q

step "cartography smoke (cartography-smoke job)"
pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q

step "ALL LOCAL GATES PASSED"
