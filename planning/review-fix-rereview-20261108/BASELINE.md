# BASELINE — review-fix-rereview-20261108

- 核查时间：2026-11-08（实时 `git fetch origin --prune` 后）
- origin/master：`585812ee4e2d75612f2b124e6fe6587af3deca5c`（Merge PR #2010）
- 本分支：`zcode/review-fix-rereview-webgis-20261108`（自 origin/master 新建）
- Worktree：`../webgis-wt-review-fix-rereview-20261108`（原主工作区 `zcode/review-g3dv2-fixes` 只读不动）
- 主工作区 dirty state：`.claude/skills/*` untracked（用户技能目录，不触碰）；`vendor/pi` submodule modified（不触碰）

## 在途 PR（全部 OPEN，实时确认）

| PR | 分支 | 主题 | 声称覆盖的 Issue |
|----|------|------|------------------|
| #2011 | zcode/goal-gis-execution-compiler-20261107 | G02 Intent→Execution Compiler | #2005 #2006 |
| #2012 | zcode/goal-pi-native-gis-runtime-20261107 | G01 Pi 原生运行时 | #1999 #2007（提及） |
| #2013 | zcode/goal-geodata-readiness-fabric-20261107 | G03 数据资格 | #1999 #2007（提及） |
| #2014 | zcode/goal-situated-gis-workbench-20261107 | G05 情境工作台 | #2004 |
| #2015 | zcode/goal-intelligent-carto-composer-20261107 | G04 制图组合器 | #1999 #2000 #2002 #2003 |
| #2016 | zcode/goal-gis-capability-ecosystem-20261107 | G06 能力生态 | #2007（提及） |
| #2017 | zcode/goal-harness-reliability-lab-20261107 | G07 可靠性 | #1999 #2001 #2003 #2005 #2006 #2007（提及） |

## Open Issues（全部被在途 PR 引用 → 全部进保护矩阵）

- #1999 A4 导出约束求解 / #2000 版面标记污染默认 PDF / #2002 组件族锁 / #2003 指北针旋转 → #2015
- #2004 框选截断 → #2014
- #2005 网络距离分区聚合错列 / #2006 summary 丢分区数值 → #2011（#2017 亦提及）
- #2007 OpenAPI 快照过期 → #2012/#2013/#2016/#2017 均提及
- #2001 换城市旧图层 → #2017 提及

## 技术栈

- Python 3.13（requires >=3.12,<3.14），pytest 9.1.1，pydantic 2.13.4；主仓 `.venv` 复用（worktree 不自建）
- 前端：frontend/（TS/TSX，pnpm lockfile）
- 本地门禁脚本：scripts/ci-local.sh（与 CI lane 对齐）；ruff/mypy/eslint/vitest
- 测试规模：tests/ 2336 个 py 文件；tests/unit/gis_harness/ 单目录 3332 tests
