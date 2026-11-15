# G42 PR_HANDOFF

- **分支**：`zcode/g42-goal-repair-execution-20261115T124804Z`
- **Worktree**：`/home/kevin/projects/webgis/webgis-worktrees/g42-goal-repair-execution-20261115T124804Z`
- **Base**：`655f769ca17f17c5797da13b4c299b8fa600e370`（origin/master，任务期未移动）
- **Head**：（commit 后填充）
- **PR**：（创建后填充）

## 复现要点

```bash
# 单测（隔离 sqlite + 内存 store + 真实 G18/G20/export 服务）
pytest tests/unit/gis_harness/goal_repair_execution/ -q

# 真实 Pi 旅程（wave=1；需已构建 vendor：bash scripts/build-vendor-pi.sh）
PI_JOURNEY_E2E=1 TOOL_WAVE_CONCURRENCY=1 TOOL_WAVE_SESSION_CONCURRENCY=1 \
  pytest tests/integration/test_pi_g42_goal_repair_journey.py -q

# 浏览器面板旅程
cd frontend && PLAYWRIGHT_MODE=mock npx playwright test \
  e2e/journeys/j42-goal-repair-panel.journey.ts --workers=1
```

## 关键决定索引

- BASELINE.md：核查事实与 6 个真实缺陷（含 2 个 G28 既有生产 bug）。
- PLAN.md / INTERFACES.md：模块布局与接口契约。
- REVIEW.md：独立评审 2 轮记录（P1×2/P2×5 修复 + 复审）。
- TEST_MATRIX.md：R01–R38 → 测试映射与层级诚实标注。
- LOOP_LEDGER.md：轮次账本与资源纪律。

## 兄弟 PR 兼容

- #2126（G43 context receipts）/ #2125（G47）/ #2165（G48）：无语义交集；
  共享文件（main.py / tools/__init__.py / chat i18n / api-stubs）均为加性行，
  合并时如遇行级冲突按双方保留处理。
