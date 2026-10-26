# G3D04 · Agent 三维意图理解、工具组合与任务执行闭环 —— 基线记录

- **日期**：2026-10-26
- **基线 SHA**：`1115d683a5c8343e44d8a9777e4ce05e073c29e5`（`git fetch origin master --prune` 后实测，与快照一致，origin/master 未推进）
- **分支**：`zcode/g3d04-agent-harness-3d-v1`（独占 worktree `../webgis-wt-g3d04-agent-harness-3d`）
- **主 clone 状态**：master 干净（仅 `.claude/skills/*` 未跟踪，与本方向无关，不进入提交）

## 基线时代码事实（file:line 于基线 SHA 核对）

### 契约层已有 3D 骨架（复用，不重建）

| 事实 | 位置 |
|---|---|
| MapSpec v1.4 `scene`（`mode∈{2d,2.5d,3d}` / `terrain{source,exaggeration,elevation_ref}` / `camera{pitch,bearing,transition_ms}` / `degrade_to`） | `app/lib/cartography/mapspec_schema.py:87,244,261,273` |
| 逐层挤出通道 `MapSpecLayerExtrusion`（height_field/stats/elevation_ref 证据面） | `app/lib/cartography/mapspec_schema.py:327` |
| 确定性 representation 决策核 `plan_scene`（证据门 fail-closed + 媒介门 + 相机推荐 + 夸张钳制） | `app/lib/cartography/scene_planning.py:116` |
| 场景质量门（9 封闭 code，未接 completion/settle） | `app/lib/cartography/scene_quality.py:25-35` |
| Pi 工具 `plan_map_scene`（只读决策）/ `set_map_scene`（事务写 scene） | `app/tools/scene_tools.py:38,141` |
| SetSceneIntent 事务管线（锁/CAS/dedup/checkpoint/回滚） | `app/services/mapspec_store.py:176`、`app/contracts/mapspec_intents.py:33` |
| Interaction IR `viewport_settled` 已带 `bearing/pitch`；mutation 链接族 layer_visibility_* | `app/services/gis_harness/interaction_ir/contracts.py:100-166` |
| RenderWork 信封 + Apply ACK（无 scene 面；scene 不进信封） | `app/lib/cartography/render_work_envelope.py:351-358`、`app/lib/cartography/render_apply_ack.py` |
| viewshed：算法 `terrain.viewshed` + capability `terrain_viewshed` + 工具 `viewshed_analysis` | `app/lib/gis/algorithms/terrain.py:295`、`app/lib/gis/capabilities/terrain.py:30`、`app/tools/terrain_analysis.py:494` |
| MethodUseContract / 三段科学守门 | `app/lib/gis/method_use_contract.py:46`、`app/lib/gis/science_final_gate.py` |
| 工具 8 面对账引擎 + 活 registry 棘轮 + CLI | `app/lib/gis/tool_capability_conformance.py:83`、`tests/unit/gis_tool_conformance/test_live_registry_audit.py`、`scripts/check_tool_capability_conformance.py` |
| G05 任务验收 lab（21 场景/19 族、真实 dispatch 缝、9 类失败归因、pin CLI） | `tests/acceptance/gis_agent/{contracts,corpus,runner,attribution}.py`、`scripts/g05_task_acceptance.py` |
| dataset roles 词表含 `elevation`（15 角色） | `app/services/gis_harness/workflow_schema.py:34` |
| Pi result ABI `pi_result.v1`（ref-first ≤4KB） | `app/services/chat/pi_result_abi.py:24,62` |
| 表达绑定 taxonomy（**SetSceneIntent 现为 `unbound`**） | `app/services/gis_harness/representation_binding/taxonomy.py:74` |

### 缺口（本方向要补的洞）

1. **Requirement IR 零 3D 概念**：`RepresentationSpec`（`app/services/gis_harness/requirement_ir/contracts.py:238`）只有 palette/geometry_kind/hidden/locked/pinned_channels；无 dimension(2d/2.5d/3d)、无 medium、无 vertical evidence、无 camera/terrain 偏好；`LockScope`（:75）无 scene/camera 锁 → 用户"切 3D/加地形/压视角"无法跨轮 user-wins 存活。
2. **无 3D cue 解析**：`build.py` cue 正则（:293-298）与 `intent_semantic.py` 词表均无 3d/立体/挤出/地形/视角词面；`terrain_analysis` 只是 2D 分析意图（`intent.py:60`）。
3. **SetSceneIntent 归 `unbound`**：用户 2D↔3D 切换不投影回 requirement（对比 palette/geometry_kind 均有 lock scope + 回投影）——表达绑定断链。
4. **planner/plan_graph 无 scene 面**：节点 kind 只有 requirement|analysis（`plan_graph.py:85`）；scene 工具借位 `thematic_cartography` capability，无一等 3D 能力 → catalog_discover 3D 可发现性为零。
5. **垂直证据无载体**：`has_height_attribute_evidence/has_elevation_evidence` 靠调用方手填；extrusion_model 结论与 DEM ref 注册回执无自动汇入 scene 决策的通道；`terrain_source` 手工传。
6. **无相机动作通道**：无 set_camera/fly_camera 工具；scene.camera 是建议档非事务动作；程序性相机在 Interaction IR 零回流（ADR-0227 P1-4）→ 无从感知"用户已手动调过镜头"实现 user-wins 相机裁决。
7. **无 3D 编排**：terrain→scene→camera→analysis 多步 DAG 无先例；scene_quality 未接 completion/settle。
8. **验收 lab 无 3D 族**：G05 F01–F19 与 Face 词表无 scene/3D 断言面。

## 相邻方向边界（详见 PR_OVERLAP_MATRIX.md）

- **G3D01（PR #1771 开放中）**：底图/DEM 资源治理、terrain provider 画像、PMTiles→XYZ、合成 DEM。→ 本方向只读消费其概念（terrain source 资格），不改其文件。
- **G3D02（分支 `zcode/g3d02-scene-engine-tiles-v1`，未开 PR）**：重写 `mapspec_schema.py`、`app/services/mapspec/*`、`scene_tools.py`、`render_work_envelope.py`、前端 `scene3d/`。→ 本方向不改这些文件；scene 面 ACK/资产挂载属其范围，本方向以 adapter + 现有 evidence 面为界。
- **G3D03（分支无提交）**：科学分析本体。→ 本方向复用既有 `terrain.viewshed`/`viewshed_analysis`，不写第二套算法。
