# BASELINE — 03 多源空间联动探索、指标钻取与 Agent 交互分析

建立时间：2026-11-27 (UTC)

## 仓库基线（B 节核实，全部真实执行）

- 目标仓库 clone：`/home/kevin/projects/webgis/webgis-ai-agent`（bare 结构 `.bare` + 多 worktree）
- `origin/master` 初始 SHA：`59eb3f52747e865b29243af305abf6bb09fcc83b`（#2282 merge 后）
- 与任务书快照一致；无更新提交。
- 最近 first-parent 25：#2282 (ui-workbench-polish) ← #2281 (整合 #2267–#2280) ← #2266 (G21 OD flow) ← #2265…
- `vendor/pi` gitlink：`160000 commit ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`
- `.gitmodules`：vendor/pi → https://github.com/earendil-works/pi.git

## PR 去重（gh 真实查询，2026-11-27）

- 全量 PR 至 #2282，**开放 PR = 0**。
- #2281 已整合 #2267–#2280；其中 #2273 为「03｜多源空间数据融合与可信数据融合环境」——
  属**数据融合线**（feat/gis-trusted-data-fusion-20261124），**非本线**（本线是联动探索，
  E1 明确不重复 data_join/融合）。
- #2272/#2274/#2275/#2277–#2280 分别为 02/04/05/01 线与拓扑、制图设计系列、模型工作流。
- **无任何本方向（multi-source linked exploration）的开放或近期未合并分支**。
- G46 单源来自 #2169（zcode/g46-linked-exploration-20261115T154748Z，已合并）。
- 结论：无重复实现风险；本线从 master 59eb3f527 新建。

## Pi 上游（B4 核实）

- `git ls-remote --tags`：最新 tag `v1.6.1` → `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`
- `gh release view --repo earendil-works/pi`：v1.6.1，publishedAt 2026-11-25，
  isPrerelease=false, isDraft=false
- **gitlink == 最新正式 release**。本线不修改 gitlink（01 线职责），只用 master 已验证 API。

## 本线 worktree

- 分支：`zcode/goal-multi-source-linked-exploration-20261126`
- worktree：`/home/kevin/projects/webgis/webgis-multi-source-linked-exploration-20261127`
- 基于 `59eb3f52747e865b29243af305abf6bb09fcc83b`；`vendor/pi` 子模块已 init（ef4a1cf）

## R0 干净基线（真实命令与结果）

| 检查 | 命令 | 结果 |
|---|---|---|
| G46 单元+集成 | `pytest tests/unit/test_g46_contract.py tests/unit/test_g46_filter_semantics.py tests/unit/test_g46_buckets.py tests/unit/test_linked_exploration_tool_annotations.py tests/integration/test_g46_service_journey.py -q` | **77 passed** (106.65s) |
| Pi G46 journey | `pytest tests/integration/test_pi_g46_linked_exploration_journey.py -q -rs` | 1 skipped — `journey is opt-in (PI_JOURNEY_E2E=1) — explicit skip, never silent-green` (82s) |
| Ruff 本域 | `ruff check app/services/linked_exploration/ app/tools/linked_exploration_tools.py app/api/routes/linked_exploration.py tests/unit/test_g46_*` | All checks passed |

- 历史披露（#2281 描述 29 项 Ruff、#2282 描述 5 项既有诊断）未在本域文件复现；
  本域相关文件 Ruff 全绿。其余基线诊断与本域无关时不扩线。

## 既有 G46 交付边界（来自 planning/G46-LINKED-EXPLORATION/PR_HANDOFF.md）

- 单源两图一地图联动、typed where（feature_pages evaluator）、条件 chips、facet 两套口径
  （图内=排除自身条件；匹配总数=全条件）、命名视图保存/重开（源锚校验 + CAS）、
  Pi 工具 ×7（inspect/preview/view_save/views/view_open/view_update/apply/export）、
  SVG/PNG/PDF 导出、exploration_views 迁移（g46_linked_exploration_views）。
- **明确不做**（当时记录）：跨源关联（G38 data join）、字段合同（G44）、坐标（G45）。
- → 本线增量 = 多源关系约束下的联动 + 跨粒度钻取 + 指标语义 + 快照作用域传播，
  复用上述全部既有面，不重写。

## 测试环境

- 解释器：`/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.12.15，非 editable，
  以 rootdir 注入方式从 worktree 运行；venv 只读复用，不修改）
- 资源约束：`OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2`；pytest 默认单进程。
