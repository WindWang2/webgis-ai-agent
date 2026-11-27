# BASELINE — GOAL-20261126-04-CARTOGRAPHIC-DIAGRAM-SYMBOLS

## 仓库基线（2026-11-27 复核，非任务书快照盲从）
- origin/master = `59eb3f52747e865b29243af305abf6bb09fcc83b`（Merge PR #2282 codex/ui-workbench-polish），与任务书编制时一致，无新合并。
- vendor/pi gitlink = `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe` = 上游 tag **v1.6.1**（正式 release，publishedAt 2026-11-25，非 draft/prerelease，`gh release view` + `git ls-remote --tags` 双重验证）。submodule 已在本 worktree 初始化。
- 默认分支 master 未变。

## PR 去重证据（gh 实查，2026-11-27）
- 开放 PR 仅 2 个：
  - #2284 `zcode/full-code-review-fixes`（全量逐行审查修复，主工作树所在分支——**本线绝不触碰**）
  - #2283 `zcode/goal-pi-native-decision-governance-20261126-172144`（01 线）
- #2282、#2281 及 #2267–#2280 已合并；#2274（04 线多尺度综合）、#2278（设计规范/系列）、#2273（03 数据融合）、#2272（02 算法库）、#2275（05 情境 Harness）、#2266（G21 OD flow）、#2265 集成 PR 均已在 master。
- 无任何 diagram/glyph/symbol/pie/chart 命名分支（本地+远程 grep 为空）。
- 兄弟线在飞（无开放 PR，纯本地 worktree）：03 线 multi-source-linked-exploration、02 线 task-data-coverage-supply —— 本线与之只读边界、不依赖其未合并代码。

## 本线分支与 worktree
- 分支：`zcode/goal-cartographic-diagram-symbols-20261126-20261126T193111Z`
- worktree：`/home/kevin/projects/webgis/webgis-cartographic-diagram-symbols-20261126T193111Z`
- 基点：origin/master `59eb3f527`

## 待补（R0 基线测试表现）
- [ ] 后端 cartography 相关 pytest 基线表现
- [ ] Ruff 基线（#2281 描述 29 项历史诊断须本地复现归类）
- [ ] 前端 typecheck/vitest 基线（#2282 描述 5 项历史诊断须本地复现归类）
- [ ] build 基线
