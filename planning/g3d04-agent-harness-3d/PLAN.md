# G3D04 · PLAN

## 架构总览（增量语义连接，不造第二框架）

```
用户话语 ──(scene3d/cues.py 确定性双语 cue)──► Requirement IR SceneSpec（typed 3D 属性 + user-wins 锁）
                                                │
              scene3d/evidence.py（真实 ref/spec 证据核实，零编造）
                                                │
              scene3d/capabilities.py（typed 能力快照：available/unavailable+alternatives）
                                                │
              scene3d/decision.py（用户偏好 × 数据资格 × 媒介 → 2d/2.5d/3d，包既有 plan_scene 决策核）
                                                │
   ┌────────────────────────────────────────────┼──────────────────────────────────────┐
   ▼                                            ▼                                    ▼
app/tools/scene_orchestration_tools.py    scene3d/orchestrator.py（DAG）      scene3d/camera.py
 get_3d_capabilities (inspection)          acquire→qualify→scene→analysis      user-wins 相机守卫
 apply_scene_representation →SetSceneIntent →attach→verdict（取消/恢复/幂等）  （编译为 SetViewIntent）
 fly_camera →SetViewIntent(+scene.camera)       │
 run_3d_analysis →viewshed_analysis 真实 dispatch│
                                                ▼
              scene3d/staleness.py（Interaction IR → 旧结果失效/重算）
              scene3d/verdict.py（MethodUseContract 科学门 × scene_quality 显示门 → completion verdict）
```

全部场景 mutation 编译为**既有** intent（SetSceneIntent/SetViewIntent）；不扩大 MutationIntent union；不碰 G3D02 分支文件（mapspec_schema / app/services/mapspec / scene_tools / 前端）。

## 里程碑（每个 = 一个原子 commit + 定向测试）

| # | 里程碑 | 交付物 | 测试面 |
|---|---|---|---|
| M1 | 3D 意图语料与断链报告 | `scene3d/corpus.py`（32 场景 machine-readable）+ `planning/.../TRACE.md`（3 条真实链路逐环追踪）+ INTENT_SCENARIOS 缺口表 | 语料完整性测试 |
| M2 | Requirement IR 3D typed attributes | `requirement_ir/contracts.py`：`SceneSpec`（dimension/terrain/extrusion/exaggeration/analysis_goals/dataset_roles/vertical_evidence/camera 偏好）+ `OutputSpec.medium` + LockScope `scene_mode`/`scene_camera`；`patch.py` 白名单+锁路径；`build.py` genesis/edit cue 发射；`scene3d/cues.py` | 契约 roundtrip/cue 32 场景/user-wins/幂等/负例 |
| M3 | 能力发现 + representation 决策 + 绑定回投影 | `scene3d/evidence.py`、`scene3d/capabilities.py`、`scene3d/decision.py`；taxonomy SetSceneIntent→requirement_significant(scene_mode)；semantic_projection 回投影；capability pack `capabilities/scene3d.py`（一等 3D 能力→catalog_discover 可发现）；plan_graph kind=scene | 决策表正反例/降级诚实/回投影/能力快照 |
| M4 | Pi 工具面（编译为既有 intent） | `app/tools/scene_orchestration_tools.py`：`get_3d_capabilities`/`apply_scene_representation`/`fly_camera`/`run_3d_analysis`（capability 门→registry.dispatch viewshed→refs+证据）；categories.py 登记；`__init__.py` 注册 | Pi dispatch 缝 E2E/8 面对账/user-wins 相机抑制/typed decline |
| M5 | 多步编排 DAG | `scene3d/orchestrator.py`：资源受控 DAG（acquire_elevation→qualify→build_scene→run_analysis→attach_result→final_verdict），进度/取消/恢复锚点载荷/幂等 ref | DAG 不变量/取消/恢复/重复执行不双倍突变 |
| M6 | 双门 verdict + 交互失效 | `scene3d/staleness.py`（viewport_settled/feature_selected/layer_visibility → typed stale）；`scene3d/verdict.py`（科学门×显示门×渲染证据→completed/degraded/blocked，绝不无证据宣称完成） | 反证测试（撤守卫应红）/失效重算 |
| M7 | E2E 场景族 | G05 lab 新族 F20–F26（≥25 场景，≥10 含交互/重入/恢复；负例 expect-fail）+ 重新 pin | `pytest tests/acceptance/gis_agent -m cartography` |

## 验证命令集（每里程碑后定向跑，收口全跑）

```bash
pytest tests/unit/gis_harness/scene3d tests/unit/gis_harness/test_requirement_ir*.py --no-cov -q   # 领域单测
pytest tests/unit/gis_tool_conformance --no-cov -q                                                # 8 面对账棘轮
python scripts/check_tool_capability_conformance.py --tool get_3d_capabilities （每个新工具）
python -m app.lib.gis.execution_catalog_docs                                                     # catalog 再生
python scripts/check_generated_staleness.py --update && pytest tests/quality/test_generated_artifact_graph.py --no-cov -q
pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 --timeout-method=thread -q
ruff check app/ tests/ scripts/ && python -m mypy
```

## 边界与降级

- attach_scene_asset（3D 模型/gltf 挂载）→ G3D02 主责，本方向以 adapter 接口（orchestrator 步骤占位 + typed unavailable）为界。
- terrain provider 画像/瓦片路由 → G3D01（PR #1771）；本方向只做"给定合格 raster-dem 源 → scene.terrain 绑定"的选择编译。
- scene 面 RenderWork/ACK 扩展 → G3D02；本方向消费既有 evidence 面（is_compiled/mutation_revision/scene_applied）。
- 真实 Pi 集成 smoke 仅在环境可用时；默认离线 deterministic（scripted dispatch 缝）。
