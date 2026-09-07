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
| USER_INTENT | — | contract-only（缺口：无生产代码填充） |
| PARSED_INTENT | — | contract-only（缺口：无生产代码填充） |
| TASK_ONTOLOGY | — | contract-only（缺口：无生产代码填充） |
| DATA_PROFILE | — | contract-only（缺口：无生产代码填充） |
| CANDIDATE_WORKFLOWS | — | contract-only（缺口：无生产代码填充） |
| SELECTED_WORKFLOW | — | contract-only（缺口：无生产代码填充） |
| TOOL_SURFACE | — | contract-only（缺口：无生产代码填充） |
| MODEL_ROUTING | app/services/chat/model_routing_bridge.py | runtime-populated |
| TOOL_CALLS | app/agent_pi_bridge.py | runtime-populated |
| ARGUMENTS | app/agent_pi_bridge.py | runtime-populated |
| TOOL_RESULTS | app/agent_pi_bridge.py | runtime-populated |
| ARTIFACT_CREATION | — | contract-only（缺口：无生产代码填充） |
| MAP_MUTATIONS | app/agent_pi_bridge.py | runtime-populated |
| MAP_OBSERVATION | — | contract-only（缺口：无生产代码填充） |
| VERIFICATION | — | contract-only（缺口：无生产代码填充） |
| REPAIR | — | contract-only（缺口：无生产代码填充） |
| FINAL_VERDICT | — | contract-only（缺口：无生产代码填充） |
| USER_OUTPUT | — | contract-only（缺口：无生产代码填充） |

> contract-only 阶段是已声明的 trace 缺口：契约先行，填充随各主线演进；认证不得为未填充阶段伪造 passed。

- 内容指纹：`f87a1137d4a33733…`
