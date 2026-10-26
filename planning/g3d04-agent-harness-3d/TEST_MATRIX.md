# G3D04 · TEST_MATRIX

## 新增测试（本方向）

| 套件 | 文件 | 覆盖 | 结果 |
|---|---|---|---|
| cue 语料 | `tests/unit/gis_harness/scene3d/test_cues_corpus.py` | 32 场景投影（参数化）+ 确定性 + 零推断红线 + 方位/俯仰/夸张/媒介语义 | 41 通过 |
| Requirement IR | `tests/unit/gis_harness/scene3d/test_requirement_ir_scene.py` | genesis/edit 投影、patch 白名单+值校验、user-wins（owner/锁/换锁）、digest 条件包含、义务面、幂等、回放不变式、锁携带 | 18 通过 |
| 决策/相机/失效/verdict | `tests/unit/gis_harness/scene3d/test_decision_camera_staleness_verdict.py` | 证据 fail-closed、能力快照（unavailable+alternatives）、决策表（含诚实降级反证）、相机 user-wins（锁等值放行/偏离抑制/viewport 活跃抑制）、失效（观察点/DEM/隐藏层/相机不失效）、双门 verdict（completed/degraded/blocked 反证） | 29 通过 |
| 编排 DAG | `tests/unit/gis_harness/scene3d/test_orchestrator.py` | DAG 结构/依赖推断/ready 推进/skip 解锁/取消语义/恢复载荷 ref-first/确定性/typed 占位 | 7 通过 |
| 工具面（真实 dispatch 缝） | `tests/unit/gis_harness/scene3d/test_scene_orchestration_tools.py` | 4 工具经 registry 真实 dispatch：能力探测、表达应用（dry-run/事务/降级）、相机（应用/viewport 抑制反证）、分析（decline×3 + 合成 DEM 真实 viewshed + MethodUseContract + 失效绑定） | 11 通过 |
| G05 验收 lab | `tests/acceptance/gis_agent`（F20–F26，26 新场景，全库 47） | 意图→IR→决策→mutation→交互→重入/恢复 全链（真实 dispatch + settle 缝 + Interaction IR 注入） | 45 通过（含 corpus/contracts） |
| 平台绑定 conformance | `tests/unit/gis/test_platform_capabilities.py`（+3 节点） | scene3d 能力×工具双向绑定声明 | 通过（全套） |

## 反证测试锚（撤防护应红）

- 零推断：`test_zero_inference_red_line` / f20-03（若 cue 层推断 3D 即红）
- 无证据不 3D：`test_decision_user_3d_without_evidence_degrades_but_keeps_preference` / f21-02
- 悬空 terrain 防线：`test_decision_terrain_without_bindable_source_omits_terrain` / f21-05 / f26-03
- 相机 user-wins：`test_camera_suppressed_by_user_viewport_divergence` / `test_camera_locked_axis_allows_user_preference_value`（偏离值）/ f22-02
- 完成判据诚实：`test_verdict_blocked_without_render_receipt_or_execution_evidence` / `test_verdict_user_degradation_visible_even_when_display_na` / f26-03
- 观察点失效：`test_staleness_observer_moved_requires_recompute`
- 负例注入有效：f21-05/f23-01..04 `expect_error=True`（decline 必须真的在 dispatch 缝失败）

## 回归面（本方向触碰的存量）

| 套件 | 结果 |
|---|---|
| `tests/unit/gis_harness`（全量） | 通过 |
| `tests/unit/gis` + `tests/unit/gis_tool_conformance` | 通过 |
| `tests/unit/gis_harness/requirement_ir` / `representation_binding` / `interaction_ir` | 通过（1 处旧断言按新语义更新：SetSceneIntent unbound→scene_mode） |
| `tests/unit/test_scene_tools.py` / `test_clarification_loop.py` | 通过 |
| `tests/quality/test_generated_artifact_graph.py` + catalog drift | 通过（账本/catalog 已再生） |
| ruff 全库 / mypy（allowlist 棘轮） | 通过 |

## 验证命令（复现）

```bash
.venv/bin/python -m pytest tests/unit/gis_harness/scene3d tests/unit/gis_harness/requirement_ir tests/unit/gis_harness/representation_binding -q --no-cov
.venv/bin/python -m pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 --timeout-method=thread -q
.venv/bin/python -m pytest tests/unit/gis_tool_conformance -q --no-cov
.venv/bin/python scripts/check_tool_capability_conformance.py --tool <四个新工具各一>
.venv/bin/ruff check app/ tests/ scripts/ main.py manage.py && .venv/bin/python -m mypy
```
