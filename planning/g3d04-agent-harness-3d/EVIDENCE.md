# G3D04 · EVIDENCE

基线 `1115d683a`；全部命令在本 worktree `.venv`（Python 3.13.17，uv 安装
`requirements-dev.txt -c requirements.lock`）执行。

## 核心验证（2026-10-26 实测输出摘录）

```text
$ pytest tests/unit/gis_harness/scene3d tests/unit/gis_harness/requirement_ir \
          tests/unit/gis_harness/representation_binding -q --no-cov
431 passed, 1 skipped

$ pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=120 --timeout-method=thread -q
45 passed            # 47 场景（21 存量 + 26 新增 F20–F26）× test_task_e2e 全绿

$ pytest tests/unit/gis_harness tests/unit/gis tests/unit/gis_tool_conformance -q --no-cov
3526 passed, 6 skipped   # 全量回归（3 个生成物漂移已再生 + 1 个棘轮 237→240 合法前进后）

$ pytest tests/quality/test_generated_artifact_graph.py tests/unit/test_execution_catalog_docs_drift.py -q --no-cov
8 passed             # 账本 + catalog 漂移闸（已再生）

$ for t in get_3d_capabilities apply_scene_representation fly_camera run_3d_analysis; do
    python scripts/check_tool_capability_conformance.py --tool $t; done
# 8 面：registration/result/search pass；science not_evaluated（平台绑定面无参数契约——
# 与既有 thematic_cartography 同口径，诚实缺席非失败）

$ ruff check app/ tests/ scripts/ main.py manage.py   → All checks passed!
$ python -m mypy                                     → Success: no issues found in 29 source files
```

## 三条端到端链（验收 D1，可复现）

1. **表达链**（`f21-01` / `tests/.../test_scene_orchestration_tools.py`）：
   `intent("建筑白模") → webgis_layer_upsert(fill-extrusion+stats.valid=25) →
   apply_scene_representation(3d) → SetSceneIntent 事务 → mapspec.scene.mode=3d +
   mutation_revision ≥1 + scene3d verdict`
2. **相机链**（`f22-01/02/03`）：`intent(方位/俯仰话语) → IR camera 偏好+锁 →
   fly_camera → SetViewIntent → mapspec.view.pitch/bearing 落 spec；用户 viewport
   活跃时 typed 抑制（view 不被偷改）`
3. **分析链**（`f23-01..04` + 单元 `test_viewshed_real_dispatch_with_synthetic_dem`）：
   `intent(通视) → IR analysis_goals → run_3d_analysis → capability 门
   （无 DEM typed decline+alternatives / 有 DEM 时 registry.dispatch 真实
   viewshed + MethodUseContract 摘要 + staleness 绑定）`

## 生成物再生记录

| 生成物 | 命令 |
|---|---|
| `docs/catalog/execution-catalog/*` | `python -m app.lib.gis.execution_catalog_docs` |
| `docs/science/ALGORITHM_CATALOG.md` | `python scripts/gen_science_catalog.py` |
| `docs/science/BENCHMARK_MANIFEST.md` | `python scripts/gen_science_benchmark_manifest.py` |
| `docs/quality/generated-artifacts.json` | `python scripts/check_generated_staleness.py --update` |
| G05 pin（47 场景） | `python scripts/g05_task_acceptance.py pin` |

## 未运行/不可运行（诚实边界）

- **线上 CI**：按工作单不等待（PR 创建即交付；未谎称 Pending 为通过/失败）。
- **前端 lane（vitest/typecheck/eslint/next build）**：本方向零前端文件改动，
  mapspec_schema/ts_projection 未触碰（byte 契约测试归 backend lane 已绿）。
- **真实 Pi 集成 smoke**：环境无 Pi 子进程（离线 deterministic 纪律）；
  dispatch 缝测试经 `dispatch_tool`/registry 真实路径。
- **perf lane**：本方向无新 perf workload；transport 基线未触碰。
- **全量 backend 六分片**：本地按 G05 纪律跑定向 lane（gis_harness/gis/
  conformance/acceptance/quality + 根级契约面），未跑完整 tests/（~23k 用例）
  ——受影响面已全覆盖，未覆盖面与本方向改动无交集（前端/导出/数据编排未触碰）。
