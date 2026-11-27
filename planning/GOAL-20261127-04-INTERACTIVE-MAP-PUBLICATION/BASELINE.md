# BASELINE — GOAL-20261127-04 交互式只读专题地图发布闭环

## 启动审计（2026-11-28 UTC）

- 目标仓库：`https://github.com/WindWang2/webgis-ai-agent`（已确认 remote origin）
- 参考快照 master：`f34cb69dda6150ee4135e6d2a476708149d9eef7`
- 实际 `origin/master`（fetch --prune 后）：`f34cb69dda6150ee4135e6d2a476708149d9eef7` — 与参考快照一致
- vendor/pi gitlink：`ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`（对应 Pi v1.6.1，2026-11-25 参考版；本线只消费运行能力，不改 vendor/pi）
- `.gitmodules`：仅 `vendor/pi` → `https://github.com/earendil-works/pi.git`
- alembic 单 head：`aa356ce2fcdc`（merge of `0110_data_fusion_authoring` + `m0111_model_drafts` + `g51_map_series`）
- 主工作树：`/home/kevin/projects/webgis/webgis-ai-agent`（分支 `zcode/full-code-review-fixes`，含未跟踪 `showcase/` — 只读不动）
- 本任务 worktree：`/home/kevin/projects/webgis/webgis-interactive-map-publication-20261127`
- 本任务分支：`zcode/goal-interactive-map-publication-20261127`（新建，基于 origin/master）

## PR 审计

- 开放 PR：仅 #2290（01 线 Pi 自适应执行，`zcode/goal-pi-adaptive-execution-20261127`）。变更面：Pi host/provider/model-routing/session-model（33 文件）。与本线（发布域）无文件交集；其迁移 `g5201_turn_attempts` 不在 master，本线迁移锚定 `aa356ce2fcdc`。
- 近期合并：#2272–#2289 全部于 2026-11-26/27 并入 master。与本线同域（04 线）已合并：#2274（多尺度综合）、#2278（设计规范/系列地图）、#2287（地理统计图符）。#2289（会话文件面板）涉及工作台入口。详见 DEDUP_MATRIX.md。
- `git merge-base --is-ancestor` 核验：本任务不接管任何现有 PR，全部只读审查。

## 主工作树状态（不触碰）

- `zcode/full-code-review-fixes` @ aedf3ef9d，未跟踪 `showcase/`。该分支为本机另一任务所有，仅 fetch 用。

## 环境记录

- platform: linux x64, zsh, CachyOS (kernel 7.3.6-1)
- 编译/测试资源纪律：CMAKE_BUILD_PARALLEL_LEVEL=4 / MAKEFLAGS=-j4 / MAX_JOBS=4 上限，竞争时降 -j2/-j1；pytest 低并发；显式限制 BLAS/OMP/GDAL 线程。
- 同机并行任务：约 15 个兄弟 worktree 活跃；共享资源池，默认单重进程，独立端口/namespace/临时目录。

## 基线既有失败（测试基线，运行后回填）

- [ ] 待运行 `pytest` 相关子集 + `vitest` 相关子集后记录既有失败清单。
