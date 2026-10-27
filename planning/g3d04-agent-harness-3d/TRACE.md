# G3D04 M1 · turn 级实测链路追踪（基线断链 → 本方向接通）

对三条典型需求（"展示成都市立体地形 / 添加建筑白模 / 计算塔台通视并制图"）沿
`user message → requirement_ir → planner → plan_graph → Pi tools → dispatch_tool →
action_ir → mapspec_store → renderwork → settle → replay/quality` 逐环实测。
**基线**列 = master `1115d683a` 实测断点；**本方向**列 = 本 PR 后状态（全部有测试锚）。

## 链 A：展示成都市立体地形

| 环节 | 基线（master） | 本方向（本 PR） | 证据 |
|---|---|---|---|
| user message → cue | 无 3D 词面解析（`build.py` cue 正则仅 2D 表达） | 双语确定性 cue：dimension=3d + terrain | `scene3d/cues.py`；`test_cues_corpus.py` s01 |
| cue → requirement_ir | **SceneSpec 不存在**（零 3D 字段） | genesis 投影 + field_provenance=user | `requirement_ir/contracts.py SceneSpec`；`test_requirement_ir_scene.py` |
| requirement_ir → 决策 | plan_map_scene 靠 agent 手填 has_*_evidence | evidence 从 spec 真实核实（raster-dem 源）→ decision 包 plan_scene | `scene3d/{evidence,decision}.py`；`test_decision_*.py` |
| 决策 → mutation | set_map_scene 存在但无编排入口 | apply_scene_representation → SetSceneIntent 事务（evidence keys） | `test_scene_orchestration_tools.py` |
| mutation → requirement 回投影 | SetSceneIntent 归 **unbound**（断链） | user origin → scene_mode 投影回 scene.dimension | `taxonomy.py`/`semantic_projection.py`；`test_binding_taxonomy.py` |
| mutation → 渲染 | scene.terrain→前端已有（ADR-0199）；**scene 面 ACK 不在信封**（G3D02） | 消费既有 evidence 面（is_compiled/mutation_revision/scene_applied）；verdict 不无证据宣称 | `scene3d/verdict.py`；G3D02 边界披露 |
| 渲染 → settle/quality | scene_quality 未接完成判据 | verdict 双门（显示门跑 scene_quality + 渲染证据） | `test_decision_camera_staleness_verdict.py` |
| 跨轮 user-wins | 用户 3D 选择无法存活 | scene_mode/scene_camera 锁 + 携带重建 + agent 不可覆盖 | `test_requirement_ir_scene.py` 5 例 |

## 链 B：添加建筑白模

| 环节 | 基线 | 本方向 | 证据 |
|---|---|---|---|
| cue | "白模/挤出"词面不存在 | extrusion_requested=True | s10-s12 |
| 高度证据 | extrusion_model 采样结论**无 IR 载体**，靠调用方记忆 | VerticalEvidenceRecord（stats.valid>0 才 verified） | `scene3d/evidence.py` |
| 无证据诚实 | 工具不核实（docstring 自认） | fail-closed：无证据→2d + SCENE_USER_3D_NO_HEIGHT_EVIDENCE（偏好保留） | `test_decision_*.py::test_decision_user_3d_without_evidence_*` |
| 3D Tiles/gltf 资产 | 无 | **typed unavailable**（G3D02 主责，adapter 占位 + alternatives） | capabilities snapshot；orchestrator s3d-asset |

## 链 C：计算塔台通视并制图

| 环节 | 基线 | 本方向 | 证据 |
|---|---|---|---|
| cue | "视域/通视"只在 terrain_analysis 分析意图（2D 衍生） | analysis_goals typed（viewshed/line_of_sight） | s25-s27 |
| 能力门 | 无：agent 可能盲调 viewshed（无 DEM 时报错不 decline | get_3d_capabilities + run_3d_analysis capability 门（unavailable + alternatives typed decline） | `test_scene_orchestration_tools.py` |
| 执行 | viewshed_analysis 存在（registry.dispatch） | 经 registry 真实 dispatch（单一执行真相）+ 合成 DEM E2E | 同上 |
| 科学面 | MethodUseContract 存在但编排不消费 | method_use_contract 摘要随结果返回（preconditions/limitations/findings） | 同上 |
| 结果上屏 | viewshed 产 stats，掩膜上屏无通道 | attach 步骤 typed 占位（G3D03 产物面）；verdict 对 stats+披露判 degraded/blocked 而非完成 | `orchestrator.py`/`verdict.py` |
| 交互后续 | 无失效语义 | 观察点移动（feature_selected）/DEM 消失 → stale+recompute；结果层隐藏 → display_stale；**相机不失效分析** | `staleness.py`；`test_*::test_staleness_*` |
| 相机编排 | 无相机工具；程序性相机 Interaction IR 零回流 | fly_camera→SetViewIntent + user-wins 守卫（viewport 偏离/相机锁抑制） | `camera.py`；tools 测试 2 例 |

## "Prompt 描述支持"vs"可执行可追踪"区分

- 基线 master 中 `plan_map_scene` 的参数文档描述了 3D 支持，但**没有一条链路从用户话
  语自动到达它**（cue/IR/证据/编排全断）——本 PR 接通；
- `line_of_sight/skyline` 在语料与能力快照中是 **typed unavailable + alternatives**，
  不是"文档里写支持"——G3D03 合入前 decline 是唯一诚实答案；
- 完整 32 场景逐条映射与缺口归属见 `INTENT_SCENARIOS.md`。
