# LOOP_LEDGER — G20 迭代账本

上下文恢复：先读本文件 + `git log --oneline` + `git diff --stat` + 实际源码。

| 轮 | 阶段 | 完成内容 | 验证 | 提交 |
|---|---|---|---|---|
| 0 | P0 | 核对 remote/默认分支；fetch --prune；`origin/master=816705218a…`；复核 PR/review/inline comments/issues；建立 8 份 planning 文档；创建独立 worktree+branch | 命令输出见 BASELINE.md | `d0…` 随首笔提交 |
| 1 | P1–P4 | `raster_expression.v1` 严格契约、MapSpec raster 子模型 + TS 生成物、owner-scoped 领域服务、真实瓦片样式消费 | 契约 18 passed；服务 10 passed；基线 208 passed/1 skipped | `a1c…` 首笔 commit |
| 2 | P5–P10 后端 | HTTP 面、Pi 工具、图例/导出、快照、SVG 孪生披露、OpenAPI/字段快照 | 新增 17 个 Python 用例；319 passed/1 skipped | `c3e403d29` 第二笔 commit |
| 3 | P6–P7 前端 | compiler/runtime numeric_raster 派生源、局部工作台 UI、i18n、地图点击广播、前端 4 组单测 | typecheck 0 / eslint 0 / vitest 111 passed | 同上第二笔 commit |
| 4 | F 第一轮独立 review | 独立 reviewer 冷读：6×P1 + 8×P2 + 5×P3，判定「不建议交付」 | 原始结论与逐项核对见 REVIEW.md | — |
| 5 | F 修复 | 6 个 P1 全部修复（单笔事务、默认 auto、逐通道 dirty、能力 URL、有界缓存/描述子、UI 全波段检视）；P2-1/2/4/5/6、P3-1/2/3/4/5 修复；P2-3/7/8 明确披露 | Python 42 passed；后端 328 passed/1 skipped/1 基线失败；前端 111 passed；ruff/typecheck/eslint/diff-check 全绿；新增 7 个评审修复回归 | 本轮 commit（见 git log） |
| 6 | F 复审 | 待：同一独立 reviewer 冷读修复后 diff | pending | pending |
| 7 | F 交付 | 待：fetch 最新 master 校正 → push → `gh pr create` → 回填 PR_HANDOFF | pending | pending |

## 当前状态（轮 5 结束）

- 分支 `zcode/goal-g20-raster-authoring-20261110T131721Z`，基于
  `816705218a5aa6202bf15d745cb31cddb3437f44`。
- 已修复所有本任务引入的 P1；剩余 P2 均为**已披露的边界**（Pi 走既有
  MapSpec 事件通道、导出仅数值栅格层、共享文件合并顺序），不是假成功。
- 两个测试失败为**基线既有**（ADR 0286/0287 撞号、registry `binding_unbacked`
  棘轮 66>64），已在 REVIEW.md / TEST_MATRIX.md 附主工作区实测证据隔离。
- 下一步：独立复审 → 如有新阻塞继续修复 → fetch 最新 master → push → PR。
- 线上 CI 按要求不等待；本地验证全部真实执行。
