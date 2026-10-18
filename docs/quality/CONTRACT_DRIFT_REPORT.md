# Contract Drift Report（自动生成）

> 由 `python scripts/gen_drift_report.py` 从各 registry 派生，请勿手改。红线：BLOCKER/MAJOR 必须清零（tests/quality/test_contract_drift_gate.py）。

- 指纹：`878a47ebc52a3bff…`
- 计数：total=129，BLOCKER=129，MAJOR=0，MINOR=0

## algorithm_contract（3）

- **BLOCKER** `ALGO_TOOL_CANDIDATE_DANGLING` platform.meta_tool_surface — tool_candidate 'deep_explore' not registered
- **BLOCKER** `ALGO_TOOL_CANDIDATE_DANGLING` platform.scenario_simulation — tool_candidate 'spatial_reasoning' not registered
- **BLOCKER** `ALGO_TOOL_CANDIDATE_DANGLING` platform.scenario_simulation — tool_candidate 'what_if_simulate' not registered

## api_frontend（126）

- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/auth/login — frontend calls /api/v1/auth/login ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/auth/logout — frontend calls /api/v1/auth/logout ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/auth/refresh — frontend calls /api/v1/auth/refresh ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/completions — frontend calls /api/v1/chat/completions ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions — frontend calls /api/v1/chat/sessions ×4 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/* — frontend calls /api/v1/chat/sessions/* ×5 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/canvas-actions — frontend calls /api/v1/chat/sessions/*/canvas-actions ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/cartographic-observation — frontend calls /api/v1/chat/sessions/*/cartographic-observation ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/chart-artifacts/* — frontend calls /api/v1/chat/sessions/*/chart-artifacts/* ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/clarifications/answer — frontend calls /api/v1/chat/sessions/*/clarifications/answer ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/clarifications/pending — frontend calls /api/v1/chat/sessions/*/clarifications/pending ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/map-action-ack — frontend calls /api/v1/chat/sessions/*/map-action-ack ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/map-state — frontend calls /api/v1/chat/sessions/*/map-state ×3 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/mapspec/mutations — frontend calls /api/v1/chat/sessions/*/mapspec/mutations ×8 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/plan — frontend calls /api/v1/chat/sessions/*/plan ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/review — frontend calls /api/v1/chat/sessions/*/review ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/table-artifacts/* — frontend calls /api/v1/chat/sessions/*/table-artifacts/* ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/visual-repairs — frontend calls /api/v1/chat/sessions/*/visual-repairs ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/visual-snapshots — frontend calls /api/v1/chat/sessions/*/visual-snapshots ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/workbench/artifact-status — frontend calls /api/v1/chat/sessions/*/workbench/artifact-status ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/sessions/*/workbench/state — frontend calls /api/v1/chat/sessions/*/workbench/state ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/skills — frontend calls /api/v1/chat/skills ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/stream — frontend calls /api/v1/chat/stream ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/chat/tools/execute — frontend calls /api/v1/chat/tools/execute ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/health — frontend calls /api/v1/cockpit/health ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/missions — frontend calls /api/v1/cockpit/missions ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/missions/* — frontend calls /api/v1/cockpit/missions/* ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/missions/*/swarm — frontend calls /api/v1/cockpit/missions/*/swarm ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/missions/*/timeline — frontend calls /api/v1/cockpit/missions/*/timeline ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/sessions/*/evidence — frontend calls /api/v1/cockpit/sessions/*/evidence ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/sessions/*/skill — frontend calls /api/v1/cockpit/sessions/*/skill ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/cockpit/sessions/*/trace — frontend calls /api/v1/cockpit/sessions/*/trace ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/config/llm — frontend calls /api/v1/config/llm ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/config/llm/test — frontend calls /api/v1/config/llm/test ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/config/rag/test — frontend calls /api/v1/config/rag/test ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/config/skills/upload — frontend calls /api/v1/config/skills/upload ×2 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog — frontend calls /api/v1/data-fabric/catalog ×3 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/* — frontend calls /api/v1/data-fabric/catalog/* ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/*/descriptor — frontend calls /api/v1/data-fabric/catalog/*/descriptor ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/*/explain — frontend calls /api/v1/data-fabric/catalog/*/explain ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/*/features — frontend calls /api/v1/data-fabric/catalog/*/features ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/*/preview — frontend calls /api/v1/data-fabric/catalog/*/preview ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/catalog/*/query — frontend calls /api/v1/data-fabric/catalog/*/query ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/materialize — frontend calls /api/v1/data-fabric/materialize ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/sources — frontend calls /api/v1/data-fabric/sources ×5 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/sources/* — frontend calls /api/v1/data-fabric/sources/* ×2 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/sources/*/probe — frontend calls /api/v1/data-fabric/sources/*/probe ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/data-fabric/sources/*/sync — frontend calls /api/v1/data-fabric/sources/*/sync ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/explorer/abort/* — frontend calls /api/v1/explorer/abort/* ×1 but no backend route matches
- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/explorer/start — frontend calls /api/v1/explorer/start ×1 but no backend route matches
- …另有 76 条，见 CONTRACT_DRIFT_REPORT.json
