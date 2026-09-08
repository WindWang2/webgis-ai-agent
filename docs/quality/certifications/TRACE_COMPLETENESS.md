# Trace Completeness Certification（自动生成）

> 由 `python scripts/gen_trace_certification.py` 派生，请勿手改。
> 契约：`app/lib/quality/trace_contract.py`；行为红线：
> `tests/quality/test_trace_completeness.py`。

## 任务类 → 必备阶段

| task class | 必备阶段 | 阶段数 |
|---|---|---|
| failure_path | FINAL_VERDICT, PARSED_INTENT, TOOL_CALLS, USER_INTENT | 4 |
| map_product | ARTIFACT_CREATION, FINAL_VERDICT, MAP_MUTATIONS, MAP_OBSERVATION, PARSED_INTENT, SELECTED_WORKFLOW, TASK_ONTOLOGY, TOOL_CALLS, TOOL_RESULTS, USER_INTENT, USER_OUTPUT, VERIFICATION | 12 |
| plan_only | PARSED_INTENT, SELECTED_WORKFLOW, TASK_ONTOLOGY, USER_INTENT, USER_OUTPUT | 5 |
| tool_execution | ARTIFACT_CREATION, PARSED_INTENT, TASK_ONTOLOGY, TOOL_CALLS, TOOL_RESULTS, TOOL_SURFACE, USER_INTENT, USER_OUTPUT | 8 |

## 18 规范阶段 × 运行时填充现状

| stage | 运行时填充位置 | 状态 |
|---|---|---|
| USER_INTENT | app/services/gis_harness/planner.py | runtime-populated |
| PARSED_INTENT | app/services/gis_harness/planner.py | runtime-populated |
| TASK_ONTOLOGY | app/services/gis_harness/planner.py | runtime-populated |
| DATA_PROFILE | app/services/gis_harness/planner.py | runtime-populated |
| CANDIDATE_WORKFLOWS | app/services/gis_harness/planner.py | runtime-populated |
| SELECTED_WORKFLOW | app/services/gis_harness/planner.py | runtime-populated |
| TOOL_SURFACE | app/services/chat/pi_native_surface.py | runtime-populated |
| MODEL_ROUTING | app/services/chat/model_routing_bridge.py | runtime-populated |
| TOOL_CALLS | app/agent_pi_bridge.py, app/services/tool_dispatch_service.py | runtime-populated |
| ARGUMENTS | app/agent_pi_bridge.py, app/services/tool_dispatch_service.py | runtime-populated |
| TOOL_RESULTS | app/agent_pi_bridge.py, app/services/tool_dispatch_service.py | runtime-populated |
| ARTIFACT_CREATION | app/services/tool_dispatch_service.py | runtime-populated |
| MAP_MUTATIONS | app/agent_pi_bridge.py | runtime-populated |
| MAP_OBSERVATION | app/api/routes/chat.py | runtime-populated |
| VERIFICATION | app/services/gis_harness/completion/pipeline.py | runtime-populated |
| REPAIR | app/services/gis_harness/completion/pipeline.py | runtime-populated |
| FINAL_VERDICT | app/services/gis_harness/completion/pipeline.py | runtime-populated |
| USER_OUTPUT | app/agent_pi_bridge.py | runtime-populated |

> contract-only 阶段是已声明的 trace 缺口：契约先行，填充随各主线演进；认证不得为未填充阶段伪造 passed。

- 内容指纹：`2d17932ba236b98b…`
