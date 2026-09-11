# Agent A Coverage — Harness / Agent / Tool Runtime (269 files, 109,886 LOC)

Census of all in-scope production files. review_status:
- `full reviewed` — entire file read line-by-line with the Read tool
- `partial reviewed` — all core state-machine / concurrency / dispatch sections read (typically >=40% incl. every lock/timeout/cancel path), remainder scanned
- `structural` — module-level structure, registrations, imports and hazard-pattern greps (blocking I/O in async, sync sleep, tier declarations, loop bounds)

Findings column = findings in this audit whose primary file is that path (see findings.md).

Note: `app/services/plan_mode.py` and `app/services/tool_dispatch_service.py` are outside the literal scope globs but sit on the audited dispatch/plan-runtime edges; they were read as dependencies (partial) and are reflected in notes.md, not counted in this census. `extensions/` contains only Python example packs (extdemo-*); the Pi extension surface (mjs/ts) lives in the `vendor/pi` submodule, which is out of repo tree. `agent/` contains skills/docs only (no production code).

| path | loc | risk | review_status | findings |
|---|---|---|---|---|
| app/agent_pi_bridge.py | 2679 | high | full reviewed | 2 |
| app/evaluation/__init__.py | 35 | medium | structural | 0 |
| app/evaluation/anti_claim.py | 326 | medium | structural | 0 |
| app/evaluation/case.py | 155 | medium | structural | 0 |
| app/evaluation/case_matrix.py | 622 | medium | structural | 0 |
| app/evaluation/chain_gate.py | 120 | medium | structural | 0 |
| app/evaluation/chaos_corpus.py | 152 | medium | structural | 0 |
| app/evaluation/closed_loop_corpus.py | 199 | medium | structural | 0 |
| app/evaluation/conformance.py | 683 | medium | structural | 0 |
| app/evaluation/failure_corpus.py | 260 | medium | structural | 0 |
| app/evaluation/fixtures.py | 157 | medium | structural | 0 |
| app/evaluation/golden_cases.py | 481 | medium | structural | 0 |
| app/evaluation/methodology_corpus.py | 240 | medium | structural | 0 |
| app/evaluation/quality_corpus.py | 1350 | medium | structural | 0 |
| app/evaluation/reliability_corpus.py | 140 | medium | structural | 0 |
| app/evaluation/replay.py | 396 | medium | partial reviewed | 0 |
| app/evaluation/report.py | 83 | medium | structural | 0 |
| app/evaluation/retrieval_corpus.py | 169 | medium | structural | 0 |
| app/evaluation/retrieval_eval_corpus.py | 1456 | medium | structural | 0 |
| app/evaluation/runner.py | 865 | medium | structural | 0 |
| app/evaluation/runtime_corpus.py | 544 | medium | structural | 0 |
| app/evaluation/runtime_metrics.py | 434 | medium | structural | 0 |
| app/evaluation/scenarios.py | 221 | medium | structural | 0 |
| app/services/cartography_runtime.py | 937 | high | partial reviewed | 0 |
| app/services/chat/__init__.py | 17 | medium | structural | 0 |
| app/services/chat/context/__init__.py | 83 | medium | structural | 0 |
| app/services/chat/context/formatters.py | 117 | medium | structural | 0 |
| app/services/chat/context/geometry.py | 33 | medium | structural | 0 |
| app/services/chat/context/history_compression.py | 261 | medium | structural | 0 |
| app/services/chat/context/layer_schema.py | 254 | medium | structural | 0 |
| app/services/chat/context/session_overview.py | 93 | medium | structural | 0 |
| app/services/chat/context_assembler.py | 715 | high | structural | 0 |
| app/services/chat/context_budget.py | 425 | medium | structural | 0 |
| app/services/chat/context_builder.py | 372 | medium | structural | 0 |
| app/services/chat/context_policy.py | 751 | high | structural | 0 |
| app/services/chat/context_projections.py | 203 | medium | structural | 0 |
| app/services/chat/decision_log.py | 140 | medium | structural | 0 |
| app/services/chat/engine_instance.py | 37 | medium | structural | 0 |
| app/services/chat/event_resume.py | 406 | high | full reviewed | 0 |
| app/services/chat/execution_engine.py | 2794 | high | partial reviewed | 1 |
| app/services/chat/llm_client.py | 761 | high | structural | 0 |
| app/services/chat/model_config.py | 90 | medium | structural | 0 |
| app/services/chat/model_routing_bridge.py | 177 | medium | structural | 0 |
| app/services/chat/model_runtime/__init__.py | 32 | medium | structural | 0 |
| app/services/chat/model_runtime/descriptors.py | 182 | medium | structural | 0 |
| app/services/chat/model_runtime/health.py | 191 | medium | structural | 0 |
| app/services/chat/model_runtime/provider.py | 186 | medium | structural | 0 |
| app/services/chat/model_runtime/roles.py | 188 | medium | structural | 0 |
| app/services/chat/model_runtime/routing.py | 257 | medium | structural | 0 |
| app/services/chat/no_progress.py | 259 | medium | structural | 0 |
| app/services/chat/pi_event_mapper.py | 359 | high | structural | 0 |
| app/services/chat/pi_native_surface.py | 536 | medium | full reviewed | 0 |
| app/services/chat/pi_rpc_client.py | 612 | medium | structural | 0 |
| app/services/chat/pi_turn_context.py | 435 | high | full reviewed | 0 |
| app/services/chat/plan_orchestrator.py | 930 | high | structural | 0 |
| app/services/chat/planner.py | 59 | medium | structural | 0 |
| app/services/chat/project_context_cache.py | 161 | medium | structural | 0 |
| app/services/chat/prompt.py | 125 | medium | structural | 0 |
| app/services/chat/schema_compression.py | 135 | medium | structural | 0 |
| app/services/chat/semantic_retrieval.py | 912 | high | partial reviewed | 0 |
| app/services/chat/session_cancellation.py | 153 | high | full reviewed | 0 |
| app/services/chat/tool_pipeline.py | 325 | high | structural | 0 |
| app/services/chat/tool_retrieval.py | 353 | high | full reviewed | 0 |
| app/services/chat/tool_semantic_retrieval.py | 177 | medium | structural | 0 |
| app/services/chat/tool_surface_v3.py | 824 | high | full reviewed | 1 |
| app/services/chat/turn_recovery.py | 75 | high | structural | 0 |
| app/services/chat/v6_context_blocks.py | 574 | medium | structural | 0 |
| app/services/collab/__init__.py | 13 | medium | structural | 0 |
| app/services/collab/artifact_status.py | 52 | medium | structural | 0 |
| app/services/collab/bus.py | 306 | high | structural | 0 |
| app/services/collab/delta.py | 240 | medium | structural | 0 |
| app/services/collab/leases.py | 321 | high | full reviewed | 0 |
| app/services/collab/presence.py | 292 | medium | structural | 0 |
| app/services/gis_harness/__init__.py | 64 | medium | structural | 0 |
| app/services/gis_harness/action_intent.py | 400 | medium | structural | 0 |
| app/services/gis_harness/analysis_graph.py | 302 | medium | structural | 0 |
| app/services/gis_harness/completion/__init__.py | 189 | medium | structural | 0 |
| app/services/gis_harness/completion/contracts.py | 519 | high | structural | 0 |
| app/services/gis_harness/completion/inputs.py | 213 | high | structural | 0 |
| app/services/gis_harness/completion/map_verification.py | 349 | medium | structural | 0 |
| app/services/gis_harness/completion/pipeline.py | 839 | high | full reviewed | 0 |
| app/services/gis_harness/completion/repairs.py | 163 | medium | structural | 0 |
| app/services/gis_harness/completion/unified_findings.py | 286 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/__init__.py | 22 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/artifacts.py | 117 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/components.py | 115 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/execution.py | 104 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/layers.py | 163 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/layout.py | 64 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/observation.py | 194 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/semantics.py | 189 | medium | structural | 0 |
| app/services/gis_harness/completion/validators/viewport_export.py | 83 | medium | structural | 0 |
| app/services/gis_harness/component_composer.py | 314 | medium | structural | 0 |
| app/services/gis_harness/component_resolver.py | 312 | medium | structural | 0 |
| app/services/gis_harness/components.py | 1263 | medium | structural | 0 |
| app/services/gis_harness/continuation.py | 198 | high | full reviewed | 0 |
| app/services/gis_harness/data_qualification.py | 540 | medium | structural | 0 |
| app/services/gis_harness/durable_context.py | 195 | high | partial reviewed | 0 |
| app/services/gis_harness/failure_taxonomy.py | 382 | high | partial reviewed | 0 |
| app/services/gis_harness/fallback_v3.py | 162 | medium | structural | 0 |
| app/services/gis_harness/gis_ontology.py | 1840 | medium | structural | 0 |
| app/services/gis_harness/goal_graph.py | 494 | medium | structural | 0 |
| app/services/gis_harness/intent.py | 897 | medium | structural | 0 |
| app/services/gis_harness/knowledge_tools.py | 210 | medium | structural | 0 |
| app/services/gis_harness/map_completion.py | 41 | medium | full reviewed | 0 |
| app/services/gis_harness/observation_states.py | 172 | high | structural | 0 |
| app/services/gis_harness/plan_candidates.py | 534 | medium | structural | 0 |
| app/services/gis_harness/plan_graph.py | 540 | high | structural | 0 |
| app/services/gis_harness/planner.py | 1476 | high | structural | 0 |
| app/services/gis_harness/planner_runtime.py | 60 | medium | structural | 0 |
| app/services/gis_harness/product_action.py | 171 | medium | structural | 0 |
| app/services/gis_harness/product_facets.py | 179 | medium | structural | 0 |
| app/services/gis_harness/product_graph.py | 716 | medium | structural | 0 |
| app/services/gis_harness/product_lineage.py | 303 | medium | structural | 0 |
| app/services/gis_harness/product_templates.py | 325 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/__init__.py | 64 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/_kit.py | 188 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/accessibility.py | 201 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/change_detection.py | 218 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/density.py | 215 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/disaster.py | 174 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/distribution.py | 334 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/environment.py | 166 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/equity.py | 173 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/exposure.py | 159 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/hydrology.py | 224 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/interpolation.py | 250 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/natural_resources.py | 198 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/network.py | 220 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/point_pattern.py | 175 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/public_health.py | 148 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/remote_sensing.py | 285 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/risk.py | 253 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/sar.py | 207 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/site_selection.py | 216 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/statistics.py | 354 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/suitability.py | 187 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/temporal.py | 215 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/terrain.py | 249 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/transport.py | 133 | medium | structural | 0 |
| app/services/gis_harness/recipe_packs/urban.py | 163 | medium | structural | 0 |
| app/services/gis_harness/recipes.py | 1079 | medium | structural | 0 |
| app/services/gis_harness/recovery_ledger.py | 354 | high | full reviewed | 0 |
| app/services/gis_harness/registry_validation.py | 422 | medium | structural | 0 |
| app/services/gis_harness/render_observation.py | 601 | high | structural | 0 |
| app/services/gis_harness/repair_planner.py | 410 | high | structural | 0 |
| app/services/gis_harness/resume_anchor.py | 462 | high | partial reviewed | 0 |
| app/services/gis_harness/resume_verify.py | 595 | high | partial reviewed | 0 |
| app/services/gis_harness/runtime_bridge.py | 817 | high | partial reviewed | 0 |
| app/services/gis_harness/runtime_repair.py | 637 | high | structural | 0 |
| app/services/gis_harness/template_catalog.py | 170 | medium | structural | 0 |
| app/services/gis_harness/template_selector.py | 199 | medium | structural | 0 |
| app/services/gis_harness/tool_surface.py | 244 | medium | structural | 0 |
| app/services/gis_harness/tools.py | 1710 | high | partial reviewed | 0 |
| app/services/gis_harness/trace.py | 185 | medium | structural | 0 |
| app/services/gis_harness/trace_store.py | 673 | medium | partial reviewed | 0 |
| app/services/gis_harness/visual_evaluator.py | 153 | medium | structural | 0 |
| app/services/gis_harness/workflow_compiler.py | 549 | medium | structural | 0 |
| app/services/gis_harness/workflow_families.py | 614 | medium | structural | 0 |
| app/services/gis_harness/workflow_instance.py | 1036 | high | partial reviewed | 0 |
| app/services/gis_harness/workflow_promotion.py | 245 | medium | structural | 0 |
| app/services/gis_harness/workflow_schema.py | 674 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/__init__.py | 37 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/acquisition.py | 196 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/cartography.py | 155 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/compiler_v4.py | 390 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/diff.py | 272 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/evaluation.py | 210 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/methodology.py | 992 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/obligations.py | 191 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/package.py | 205 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/parameters.py | 160 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/recompute.py | 198 | medium | structural | 0 |
| app/services/gis_harness/workflow_v4/typed_dag.py | 499 | medium | structural | 0 |
| app/services/jobs/__init__.py | 141 | medium | structural | 0 |
| app/services/jobs/artifacts.py | 3 | medium | structural | 0 |
| app/services/jobs/cancellation.py | 3 | medium | structural | 0 |
| app/services/jobs/context.py | 97 | high | structural | 0 |
| app/services/jobs/lifecycle.py | 192 | high | structural | 0 |
| app/services/jobs/progress.py | 208 | medium | structural | 0 |
| app/services/jobs/redaction.py | 267 | medium | structural | 0 |
| app/services/jobs/store.py | 1012 | high | structural | 0 |
| app/services/jobs/submit.py | 196 | high | structural | 0 |
| app/services/jobs/views.py | 195 | medium | structural | 0 |
| app/services/jobs/worker.py | 466 | high | partial reviewed | 0 |
| app/services/planning/__init__.py | 74 | medium | structural | 0 |
| app/services/planning/capability.py | 157 | high | structural | 0 |
| app/services/planning/deps.py | 179 | medium | structural | 0 |
| app/services/planning/followup.py | 115 | medium | structural | 0 |
| app/services/planning/models.py | 228 | medium | structural | 0 |
| app/services/planning/recovery.py | 154 | high | full reviewed | 1 |
| app/services/planning/store.py | 289 | high | partial reviewed | 0 |
| app/services/session_data.py | 924 | high | full reviewed | 0 |
| app/services/session_data_protocol.py | 438 | low | structural | 0 |
| app/services/session_data_redis.py | 1858 | high | partial reviewed | 0 |
| app/services/session_ownership.py | 28 | low | structural | 0 |
| app/services/session_plan.py | 827 | high | full reviewed | 0 |
| app/services/workflow_engine.py | 1696 | high | partial reviewed | 0 |
| app/tools/__init__.py | 99 | medium | full reviewed | 0 |
| app/tools/_utils.py | 314 | medium | structural | 0 |
| app/tools/advanced_spatial.py | 2818 | medium | structural | 0 |
| app/tools/annotation.py | 272 | medium | structural | 0 |
| app/tools/argument_normalization.py | 484 | medium | structural | 0 |
| app/tools/cartography.py | 725 | medium | structural | 0 |
| app/tools/cartography_tools.py | 897 | medium | structural | 0 |
| app/tools/categories.py | 174 | medium | structural | 0 |
| app/tools/change_detection.py | 332 | medium | structural | 0 |
| app/tools/chart.py | 518 | medium | structural | 0 |
| app/tools/chinese_maps/__init__.py | 939 | medium | partial reviewed | 0 |
| app/tools/chinese_maps/_shaping.py | 106 | medium | structural | 0 |
| app/tools/chinese_maps/amap.py | 812 | medium | structural | 0 |
| app/tools/chinese_maps/baidu.py | 359 | medium | structural | 0 |
| app/tools/chinese_maps/http.py | 239 | medium | partial reviewed | 0 |
| app/tools/chinese_maps/protocol.py | 79 | medium | structural | 0 |
| app/tools/chinese_maps/tianditu.py | 404 | medium | structural | 0 |
| app/tools/coord_transform.py | 204 | medium | structural | 0 |
| app/tools/dasymetric_tools.py | 100 | medium | structural | 0 |
| app/tools/data_discovery.py | 336 | medium | structural | 0 |
| app/tools/data_fabric_tools.py | 1059 | medium | structural | 0 |
| app/tools/descriptor.py | 537 | high | full reviewed | 0 |
| app/tools/explorer_tools.py | 114 | medium | structural | 0 |
| app/tools/flow_tools.py | 354 | medium | structural | 0 |
| app/tools/geocoding.py | 139 | medium | structural | 0 |
| app/tools/geocompute_tools.py | 222 | medium | structural | 0 |
| app/tools/harness_runner.py | 77 | medium | full reviewed | 0 |
| app/tools/ingest_tools.py | 163 | medium | structural | 0 |
| app/tools/layer_manager.py | 691 | medium | structural | 0 |
| app/tools/local_admin.py | 420 | medium | structural | 0 |
| app/tools/local_osm.py | 169 | medium | structural | 0 |
| app/tools/local_stats.py | 301 | medium | structural | 0 |
| app/tools/map_view.py | 327 | medium | structural | 0 |
| app/tools/meta_tools.py | 139 | high | full reviewed | 0 |
| app/tools/modelops_tools.py | 385 | medium | structural | 0 |
| app/tools/monitoring_report.py | 303 | medium | structural | 0 |
| app/tools/nature_resources.py | 215 | medium | structural | 0 |
| app/tools/network_tools.py | 1072 | medium | structural | 0 |
| app/tools/osm.py | 583 | medium | structural | 0 |
| app/tools/plan_mode.py | 210 | high | full reviewed | 1 |
| app/tools/point_pattern_tools.py | 894 | medium | structural | 0 |
| app/tools/policy_audit.py | 109 | medium | full reviewed | 0 |
| app/tools/project_tools.py | 478 | medium | structural | 0 |
| app/tools/raster_tools_cog.py | 135 | medium | structural | 0 |
| app/tools/registry.py | 1879 | high | full reviewed | 3 |
| app/tools/remote_sensing.py | 2667 | medium | structural | 0 |
| app/tools/report.py | 195 | medium | structural | 0 |
| app/tools/science_temporal_tools.py | 198 | medium | structural | 0 |
| app/tools/semantic_tools.py | 368 | medium | structural | 0 |
| app/tools/skill_surface_refresh.py | 91 | medium | structural | 0 |
| app/tools/skills.py | 506 | high | partial reviewed | 0 |
| app/tools/spatial.py | 466 | medium | structural | 0 |
| app/tools/spatial_decision_tools.py | 488 | medium | structural | 0 |
| app/tools/spatial_reasoning.py | 341 | medium | structural | 0 |
| app/tools/spatial_stats.py | 1844 | medium | structural | 0 |
| app/tools/subagent.py | 183 | high | partial reviewed | 0 |
| app/tools/templates.py | 839 | medium | structural | 0 |
| app/tools/temporal_tools.py | 903 | medium | structural | 0 |
| app/tools/terrain_analysis.py | 1928 | medium | structural | 0 |
| app/tools/upload_tools.py | 188 | medium | structural | 0 |
| app/tools/web_crawler.py | 315 | medium | structural | 0 |
| app/tools/what_if_rules.py | 84 | medium | structural | 0 |
| app/tools/what_if_simulate.py | 405 | medium | structural | 0 |
| app/tools/workspace_tools.py | 181 | medium | structural | 0 |
| extensions/examples/extdemo-ml-pack/health.py | 5 | low | structural | 0 |
| extensions/examples/extdemo-ml-pack/main.py | 58 | low | structural | 0 |
| extensions/examples/extdemo-pack/health.py | 12 | low | structural | 0 |
| extensions/examples/extdemo-pack/main.py | 320 | low | structural | 0 |
| extensions/examples/extdemo-pack/tile_catalog.py | 140 | low | structural | 0 |
| extensions/examples/extdemo-v3-pack/health.py | 5 | low | structural | 0 |
| extensions/examples/extdemo-v3-pack/main.py | 124 | low | structural | 0 |
| **TOTAL (269 files)** | **109886** | | | |

Risk: {'high': 54, 'medium': 206, 'low': 9}  Status: {'full reviewed': 22, 'structural': 227, 'partial reviewed': 20}
