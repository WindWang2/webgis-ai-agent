# MILESTONES / LEDGER — situated-agent-workbench-vnext

格式：`轮次 | master HEAD | changed files | baseline | implementation | regression tests | failed checks | risk | next smallest change`

| 轮 | 记录 |
|---|---|
| R0-1 | master `40cda057a`；fetch/PR/issues 全读；open PR #1972/#1965 避让面确认（见 PR_OVERLAP_MATRIX.md）；worktree 建立于 origin/master；核心文档（CLAUDE/CONTEXT/UL/gis-harness/closed-loop/ADR-0227/0268/0277/0278/0281）与 Track E 核心源码（contracts/contract/queries/consistency/turn_context）亲读；2 个探索 subagent（后端/前端）派发。 |
| R1-1 | 基线锁定：backend `pytest tests/unit/gis_harness/interaction_ir/ + tests/test_gis_situation_*.py` = **139 passed**（8.95s，Python 3.12 venv，uv 装 requirements）；frontend `vitest run lib/interaction lib/selection lib/workbench components/workbench components/agent` = **189 passed / 22 files**（11.2s）。失败：无。 |
| R3-R7 | S0-S7 实现完成（7 commits）：E0 characterization / S1 AnalysisScope+工具 / S3 选择史 / S2 IR 三连收 / S4 TurnChain+端点 / S5+S6 恢复与状态面+undo / S7 8 后端旅程+j10-j12 浏览器旅程。全量验证见 EVIDENCE.md。 |
| R8 | 独立 review（未参与实现的 subagent，全量 diff 59 文件）：**P0=0 / P1=1 / P2=6 / P3=12**。 |
| R9 | 清偿 commit 85cb5f1d4：P1 生成物再生；6×P2 全修（幽灵 selection/选择代 closer/chart 替换语义/dataset_ref 服务端解析/状态条会话切换/确认路径 undo+幂等）；P3 批量（ADR 口径/validated 标记/sensitivity/封闭性/死代码/重放界）。回归：后端 229 + 前端 106 + journeys 3 全绿。复审进行中。 |
