# PR Overlap Matrix — situated-agent-workbench-vnext

基准：origin/master `40cda057a`（2026-11-06 fetch 后）。
方法：`gh pr list --state all --limit 100` 全读 + 两个 open PR 逐文件比对 + 近 30 个
合并 PR 的文件面抽查（#1868–#1880、#1962–#1971）。

## Open PR（硬避让面）

| PR | 主题 | 与本方向文件重叠 | 处置 |
|---|---|---|---|
| #1972 | awf-v2 类型化分析产物封套 | `app/services/gis_harness/analysis_workflows/*`、`app/services/gis_harness/tools.py`、`app/tools/analysis_workflow_tools.py`、`tests/unit/gis_harness/analysis_workflows/*`、`docs/gis-harness.md`、`.goal-loop-ledger.md` | **零写入**。scoped analysis 只读消费其契约面；不碰 tools.py 注册表。 |
| #1965 | g3dv2 review fixes | `frontend/components/map/map-panel.tsx`、`frontend/lib/hooks/use-cartographic-observation.ts`、`frontend/lib/map-commands/viewCommands.ts`、`frontend/lib/map-kit/scene3d/*`、`app/api/routes/layer.py`、`app/api/routes/upload.py`、`app/schemas/chat_schema.py` | **零写入或最小接入**。map-panel 若必须接入，改前先 fetch 复查 #1965 状态，diff 最小化并独立 commit；优先走 lib/selection → interaction/bus 既有通道，不改 map-panel 内部逻辑。 |

## 近期已合并（语义边界，不重做）

| PR | 主题 | 本方向关系 |
|---|---|---|
| #1623 (ADR-0227) | Interaction IR V1：13 kind 词表 + REST 批量端点 + outbox/bus + situation compile 接线 | **底座，只补缺口不重构**。词表新增 kind 走 additive + manifest 双闸。 |
| #1873 (ADR-0277) | 受控 feature editing（preview/confirm/undo + PatchSet journal + _wfid） | 敏感编辑确认门**复用**，不另建确认通道。 |
| #1874 (ADR-0278) | map_operation.v1 受控提交/回执环 + FinalDisplayConfirmation + 指代解析 D7（focused_layer/last_result/clarification） | Agent 建议动作生命周期**复用**；指代解析扩展到 selection/chart 维度时走其 resolver 扩展点。 |
| #1875 | linked views（chart-panel/table-panel/viewport-aggregate + layer-data store） | 图表↔地图联动**复用**；刷选事件若需上报走 IR additive，不动 chart-panel 数据面。 |
| #1877 | temporal（MapSpec v1.8 time 契约 + 时间轴 UI + temporal_view_* 工具） | 时间窗事实接入 situation/ContextSnapshot；时间轴 scrub 事件是否进 IR 以现有 UI 钩子为准（ADR-0227 纪律：有真实钩子才收词）。 |
| #1879 (ADR-0281) | RecoveryPolicy/GISRecoveryDiagnostic/GoalVerification | 恢复分类**复用**；E3 恢复语义对齐其 6 处置词表。 |
| #1880 | multiscale（MapSpec v1.9） | 只读。 |
| #1876 (ADR-0279) | SpatialSemanticContract | 只读消费（scoped analysis 的语义边界）。 |
| #1878 | OGC/COG 数据源 | 无关。 |
| #1868–#1871 | data onboarding / workflows / carto design / atlas | 只读（workflow 编排归 Track B）。 |
| #1962–#1971 | CI/deploy/journey contracts 修复 | e2e journey 基建（#1968）**直接复用**其 helpers/registry 模式。 |

## 共享高冲突区（目标书 §三）

- `frontend/components/map/map-panel.tsx`：#1965 占用中 → 默认只读；确需写则最小 diff + 独立 commit + fetch 复查。
- `app/api/routes/chat.py`：非本方向所有权 → 只读；新端点放独立 route 文件（先例：interaction_events.py / turn_journal.py）。
- MapSpec schema / ToolDispatchService / ToolRegistry：零写入；scoped analysis 经现有工具参数面（feature_ids/ref/scope）表达。

## 结论

本方向主改动面（`app/services/gis_situation/`、`app/services/gis_memory/`（只读为主）、
`app/services/gis_harness/interaction_ir/`、`app/services/turn_journal/`（扩展投影）、
`frontend/lib/interaction|selection|workbench/`、`frontend/components/workbench|agent/`、
新 e2e journeys）与两个 open PR **文件级零重叠**。
