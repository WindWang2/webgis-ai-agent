# LOOP_STATE — goal-loop 账本（Track A）

## 第 N 轮状态（当前：M14 质量门）

- 目标：地图操作事务 + 显示确认 human seam 生产闭环，PR 提交不 merge。
- 已完成：硬门勘察（3 explorer + 亲读）→ BASELINE/PLAN/PR_OVERLAP_MATRIX →
  后端 map_operations 包 + 工具 + 2 路由组 + pipeline 披露（commit 1）→
  前端 bus/命令/api/卡/i18n/挂载（commit 2）→ perf+恢复+负例（commit 3）→
  ADR-0278 + TEST_MATRIX。
- 验证：后端 56/56、前端新增 18/18、回归（IR 88/auth 22/api_compat 12/
  completion 族 104/前端 393+816）全绿；typecheck 通过。
- 下步：M14 全量质量门（ruff 全量/compileall/受影响 pytest 全量/frontend
  lint+vitest 全量/git diff --check）→ 独立 review（未参与编写的 agent）
  → INDEPENDENT_REVIEW.md → push + PR（含 PR_OVERLAP_MATRIX 更新与
  rebase 检查）。
- 资源：单 worktree；前端一次性 pnpm install（warm store）；vitest
  --maxWorkers=2；pytest 非 xdist。
- 阻碍：无。已知限制见 TEST_MATRIX（Playwright 端口级 e2e 不可用）。

## 轮次记录

| 轮 | 改动 | 验证 | 结果 | 下步 |
|---|---|---|---|---|
| 1 | 硬门勘察+规划文档 | git/gh 实时证据 | ✓ | 契约 |
| 2 | contracts.py | 冒烟+18 契约测试 | ✓ | 解析器/服务 |
| 3 | resolver+service+工具+路由 | 48 测试（修 3 缺陷：sid 跨运行污染/args_model/schemas 预期） | ✓ | 前端 |
| 4 | 前端全套 | 18 新增+race 污染修复（延迟水合） | ✓ | perf/恢复 |
| 5 | perf+恢复+负例 | 8/8（1000 层 15/164ms） | ✓ | 质量门 |
| 6 | 文档（ADR/矩阵） | — | 进行中 | 独立 review |
