# PROTECTED_PATHS — 7 PR 逐文件所有权矩阵（2026-11-08 实时核查）

规则：PROTECTED = 不修改（只读 finding）；ADJACENT = 共用依赖但不改同一函数，可修但需 diff 对拍；
INDEPENDENT = 无 owner，可进修复队列。

## PROTECTED（精确文件级）

### PR #2011 (G02 execution compiler)
- app/services/gis_harness/execution_compiler/**（全部）
- app/services/gis_harness/completion/pipeline.py
- app/services/gis_harness/registry_validation.py
- app/services/gis_harness/requirement_ir/build.py, normalize.py
- app/tools/analysis_workflow_tools.py（owner 语义：#2005/#2006 修复）
- tests/unit/gis_harness/test_execution_compiler_*.py, tests/unit/gis_tool_conformance/test_live_registry_audit.py

### PR #2012 (G01 Pi native runtime)
- app/agent_pi_bridge.py
- app/api/routes/chat.py, app/api/routes/health.py
- app/lib/runtime/evidence.py
- app/schemas/health_schema.py
- app/services/chat/pi_compat.py, pi_durable_cancel.py, pi_native_surface.py, pi_rpc_client.py, pi_surface_metrics.py, pi_turn_state.py
- scripts/pi_doctor.py, tests/unit/test_pi_*.py, tests/integration/test_pi_stub_llm_journeys.py, tests/benchmarks/test_pi_perf.py

### PR #2013 (G03 geodata readiness)
- app/api/routes/upload.py
- app/lib/geo_processor/core.py
- app/lib/gis/dataset_descriptor.py
- app/services/data_fabric/adapters/**, app/services/data_fabric/registry.py
- app/services/data_ingest/geojson_guard.py, pipeline.py
- app/services/dataset_semantics/**（builder, dispatch_gate, preflight, reuse, task_scope）
- app/services/explorer/validate_stage.py
- app/services/gis_harness/spatial_semantics/inspect_semantics.py
- app/services/mapspec_store.py
- app/services/tool_dispatch_service.py（与 #2017 共享 → 极端保守，PROTECTED）
- tests/data/test_g03_*.py, tests/perf/test_g03_ingest_bounded.py

### PR #2014 (G05 situated workbench)
- app/services/gis_harness/interaction_ir/contracts.py, ingest.py, projection.py
- app/services/gis_harness/map_operations/service.py
- app/services/gis_situation/analysis_scope.py, selection_history.py（analysis_scope 与 #2017 共享 → PROTECTED）
- frontend/components/agent/agent-run-panel.tsx(+test)
- frontend/components/map/map-panel.tsx, map-harness-cards.tsx(+test), map-components/chart-panel.tsx, map-components/panel-ir-emit.test.tsx
- frontend/lib/interaction/**, frontend/lib/map-operations/**, frontend/lib/workbench/undo.ts
- frontend/messages/{en-US,zh-CN}/{map,mapChart}.json
- tests/unit/gis_harness/interaction_ir/**, tests/unit/gis_harness/map_operations/test_service.py, tests/test_gis_situation_*.py

### PR #2015 (G04 carto composer)
- app/lib/cartography/composition_templates.py, layout_solver.py, publication_constraint_layout.py, render_diagnostics.py, render_scene.py, selection_context.py, svg_marginalia.py
- app/services/agent_swarm/specialists/cartographer.py
- app/services/gis_harness/planner.py, app/services/gis_harness/tool_map_product.py
- app/services/mapspec_svg_chrome_layout.py, app/services/mapspec_to_svg.py
- app/tools/cartography.py
- frontend/lib/api/publication.ts, frontend/lib/map-kit/**（exporter/publication-export/export-chrome/constraint-layout-apply）
- tests/cartography/test_*_g04.py 等

### PR #2016 (G06 capability ecosystem)
- app/lib/gis/algorithms/**（含 __init__.py, capability_ecosystem.py）
- app/lib/gis/availability_rerank.py, capability_availability.py, capability_schema_parity.py, method_eligibility.py, provider_eligibility.py
- app/services/chat/pi_availability_adapter.py
- app/tools/__init__.py, app/tools/capability_ecosystem_tools.py
- app/lib/data/capability_lexicon_golden.json, docs/catalog/**, examples/gis_capability_ecosystem/**

### PR #2017 (G07 harness reliability)
- app/lib/harness/lab/golden.py, app/lib/harness/replay/**, app/lib/harness/result_contracts.py
- app/services/gis_harness/interaction_ir/contracts.py, projection.py（共享 #2014 → PROTECTED）
- app/services/gis_situation/analysis_scope.py（共享 #2014 → PROTECTED）
- app/services/tool_dispatch_service.py（共享 #2013 → PROTECTED）
- pyproject.toml（依赖/工具配置热点 → PROTECTED，除非 lint 必需微调且不影响双方）
- scripts/harness_*.py, scripts/perf/bench_harness_reliability.py
- tests/harness_replay/**, tests/benchmarks/test_harness_reliability_bench.py, tests/fixtures/reliability/**

### 全局
- .goal-loop-ledger.md（多 PR 共享 → 禁改，账本写本 planning 目录）
- vendor/**、frontend/package.json、pnpm-lock.yaml（不升级依赖）

## INDEPENDENT（无 owner，审计可进修复队列的重点区域）

- app/services/spatial_analyzer.py, spatial_operator.py, spatial_tasks.py, feature_aggregate.py
- app/services/network/**, app/services/geocompute/**, app/services/spatial_decision/**
- app/lib/gis/ 中不在 #2016 名单的文件（如 geometry 工具、CRS 处理）
- app/lib/geo_analysis/**, app/lib/geo_raster/**（geo_processor/core.py 除外）
- app/services/raster_*.py, mvt.py, feature_pages.py
- app/services/session_ownership.py, session_data*.py, distributed_lock.py, singleflight.py
- app/services/export_image_security.py, s3_blob_store.py, durable_blob_store.py, export_paths.py
- app/lib/tool_security.py, tool_cache.py, tool_result_errors.py, cancellation.py
- app/api/routes/ 中 upload/chat/health 之外的路由
- frontend/lib 中 interaction/map-kit/map-operations/workbench(undo)/api(publication) 之外的区域
- frontend/components 中 map/ 与 agent/ 之外的组件
- app/services/gis_harness/ 中未列入上述 PR 的子模块（如 mapspec_* 其余、gis_context、gis_world_state）

## 语义级保护（即使文件不在列表）

- 「完成/结算」语义：completion settlement、honest completion contracts（#1995 已修、#2011/#2017 在改）
- SSE 幂等/TurnPhase 状态机（#2012）
- 选择截断/范围披露（#2004/#2014）
- 出版约束求解/族锁/指北针（#1999-#2003/#2015）
- 网络距离分区聚合、summary 分区数值（#2005/#2006/#2011）
- OpenAPI 快照再生成（#2007 多 PR）
