# PR_OVERLAP_MATRIX — 实时交集（2026-10-31 gh 勘察）

开放 PR：#1868/#1869/#1870/#1871/#1872/#1873（均 base=master）。本分支文件触碰 vs 各 PR：

| 文件 | 本任务 | #1868 | #1869 | #1870 | #1871 | #1872 | #1873 | 处置 |
|---|---|---|---|---|---|---|---|---|
| app/main.py | +2 行路由注册 | ✓ | | | ✓ | | ✓ | 加法，按字母序插入，合并可解 |
| app/tools/__init__.py | +1 模块注册 | ✓ | ✓ | | ✓ | | ✓ | 同上 |
| app/tools/categories.py | +1 名字覆盖 | ✓ | | | | | ✓ | 同上 |
| app/services/gis_harness/completion/pipeline.py | 披露块加法 | | | | | | | 独占 |
| app/services/gis_harness/map_operations/**（新） | 新建 | | | | | | | 独占 |
| app/api/routes/{map_operations,display_confirmation}.py（新） | 新建 | | | | | | | 独占 |
| app/schemas/{map_operation,display_confirmation}_schema.py（新） | 新建 | | | | | | | 独占 |
| app/tools/map_operations_tools.py（新） | 新建 | | | | | | | 独占 |
| frontend/lib/map-commands/catalogue.ts | +1 slice 导入 | | ✓ | | | | | 加法一行 |
| frontend/components/map/map-panel.tsx | +1 行挂载 | | | | | | ✓ | 加法一行（#1873 改编辑交互，区域不同；rebase 时手解） |
| frontend/messages/{zh,en}/map.json | +键 | | | ✓ | | | | 加法键（#1870 改 map.json 其他键） |
| frontend/lib/map-operations/**、components/map/map-harness-cards.tsx（新） | 新建 | | | | | | | 独占 |
| tests/unit/gis_harness/map_operations/**（新） | 新建 | | | | | | | 独占 |
| docs/dev/endpoint-scope-matrix.csv | 再生 | ✓ | | | ✓ | | ✓ | 脚本再生，合并取并集重跑 |
| tests/quality/snapshots/openapi.json | 再生（若可用） | ✓ | | | ✓ | | ✓ | 同上 |
| .goal-loop-ledger.md | 本树独立 | ✓ | ✓ | ✓ | | | | 各 worktree 独立文件，PR 各自携带 |

**零触碰清单**（他 PR 独占核心）：app/services/data_preparation/、analysis_workflows/、app/lib/cartography/{component_abi,design_candidates,...}.py、app/services/atlas_batch/、app/services/gis_harness/intent_semantic.py、app/services/feature_edit/、app/services/mapspec/{intents,lifecycle_engine,mutation_handlers,mutation_registry}.py、gis_situation/{compiler,contract}.py、interaction_ir/{contracts,projection}.py、frontend/lib/{chat/adapters.ts,chat/message-reducer.ts,interaction/bus.ts,workbench/undo.ts,hooks/use-sse-stream.ts}、app/agent_pi_bridge.py、gis_harness/tools.py、requirement_ir/。

## 前置拒重核对

- #1873 有 feature_edit mapspec_stamp + 确认卡（edit-proposal-card）——那是**数据要素几何/属性编辑**的确认；本任务是**地图表现层操作事务**（显隐/样式/视口/组件），不重叠。
- #1870 design-candidates 是制图方案候选；本任务 preview 是操作前后 diff，不同层。
- #1872 intent_semantic.py 意图槽位——本任务不经由该文件（resolver 独立模块）。
- FinalDisplayConfirmation seam（display_confirmation.py）无任何开放 PR 触碰（gh pr diff 1868-1873 --name-only 核对）。
