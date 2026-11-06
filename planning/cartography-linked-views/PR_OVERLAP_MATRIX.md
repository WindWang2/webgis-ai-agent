# PR_OVERLAP_MATRIX — 2026-10-31 实测（gh pr diff --name-only）

| 文件/区域 | #1868 | #1869 | #1870 | #1871 | #1873 | Track B 决策 |
|---|---|---|---|---|---|---|
| app/services/feature_pages.py | - | - | - | - | - | ✅ 自由（主战场） |
| app/services/mvt.py | - | - | - | - | - | ✅ 自由 |
| app/api/routes/layer.py | - | - | - | - | - | ✅ 自由 |
| app/schemas/layer_schema.py | - | - | - | - | - | ✅ 自由（additive） |
| table-panel.tsx / chart-panel.tsx | - | - | - | - | - | ✅ 自由（无 PR 占用） |
| selection-store.ts / viewport-context.ts / paged-features.ts | - | - | - | - | - | ✅ 自由 |
| label_plan/engine/collision/typography.py | - | - | - | - | - | ✅ 自由 |
| mapspec_to_svg.py / label-*.ts / mapspec-to-svg.ts / exporter.ts | - | - | - | - | - | ✅ 自由 |
| component_renderers.py | - | - | ⚠️ :185 区插入 | - | - | ❌ 不碰 _SUPPORT_MATRIX |
| renderer-parity-matrix.md | - | - | ⚠️ +1 行 | - | - | ❌ 不碰表体 |
| semantic_checks*.py | - | - | ⚠️ 尾部 +10 行 | - | - | ❌ 不碰（M13 走 map_critique/render_diagnostics） |
| publication_export.py | - | - | - | ⚠️ 376 行 | - | ❌ 绝不改 |
| map-panel.tsx / use-feature-selection.ts | - | - | - | - | ⚠️ 重改 | ❌ 不碰（联动经 selection store，无需改） |
| mapspec/{intents,lifecycle_engine,mutation_handlers,mutation_registry}.py | - | - | - | - | ⚠️ | ❌ 不碰 |
| feature_edit/* / identity.py | - | - | - | - | ⚠️ 新增 | ❌ 不碰（只读对齐 FEATURE_ID_KEYS/_wfid 语义） |
| catalogue.ts / sceneCommands.ts | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ 不碰（前端命令不新增；M4 定位用组件内交互） |
| hud-types.ts | - | - | - | ⚠️ | ⚠️ | ❌ 不碰 |
| messages/*/map.json | - | - | ⚠️ | - | - | ❌ 不碰（文案走既有 key 或组件内 fallback） |
| messages/*/sidebar.json | - | - | - | ⚠️ | - | ❌ 不碰 |
| app/tools/__init__.py | - | ⚠️ | - | ⚠️ | ⚠️ | ⚠️ 单行追加，最后提交 |
| app/main.py | - | - | - | ⚠️ | ⚠️ | ✅ 不需改（复用 layer router） |
| tests/quality/snapshots/openapi.json | - | - | - | ⚠️ | ⚠️ | ⚠️ API_SNAPSHOT_UPDATE=1 最后刷新 |
| tests/acceptance/gis_agent/* | - | ⚠️ | ⚠️ | - | - | ❌ 不碰 |
| frontend/lib/api/* | - | - | - | ⚠️ (atlas-jobs) | ⚠️ (feature-edit) | ✅ 新文件 layer-browse.ts 零冲突 |

结论：Track B 与六个 open PR 的核心面无交集；仅生成物/登记表三处弱冲突，策略=最后一次性提交+PR 说明披露。

## 增补（2026-10-31 提交前复查）

新 PR **#1874（map-ops，Track A：情境化地图操作事务与可验证终验）** 与本分支文件交集 = 3，全部为登记/生成物：`app/tools/categories.py`、`app/tools/__init__.py`、`tests/quality/snapshots/openapi.json` —— 各自 additive 行/可再生快照，rebase 零语义冲突；功能面（地图操作事务/终验）与本分支（MVT 查询/联动/标注/单页导出）零重叠。origin/master 未前进（HEAD..origin/master 为空）。
