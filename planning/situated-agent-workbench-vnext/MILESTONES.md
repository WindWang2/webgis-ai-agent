# MILESTONES / LEDGER — situated-agent-workbench-vnext

格式：`轮次 | master HEAD | changed files | baseline | implementation | regression tests | failed checks | risk | next smallest change`

| 轮 | 记录 |
|---|---|
| R0-1 | master `40cda057a`；fetch/PR/issues 全读；open PR #1972/#1965 避让面确认（见 PR_OVERLAP_MATRIX.md）；worktree 建立于 origin/master；核心文档（CLAUDE/CONTEXT/UL/gis-harness/closed-loop/ADR-0227/0268/0277/0278/0281）与 Track E 核心源码（contracts/contract/queries/consistency/turn_context）亲读；2 个探索 subagent（后端/前端）派发。 |
| R1-1 | 基线锁定：backend `pytest tests/unit/gis_harness/interaction_ir/ + tests/test_gis_situation_*.py` = **139 passed**（8.95s，Python 3.12 venv，uv 装 requirements）；frontend `vitest run lib/interaction lib/selection lib/workbench components/workbench components/agent` = **189 passed / 22 files**（11.2s）。失败：无。 |
