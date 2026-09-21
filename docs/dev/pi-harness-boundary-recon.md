# Recon — 方向 9：Pi ↔ GIS Harness Boundary Convergence

> 勘察基线：`origin/master = 5a4d4632`（PR #1478 合并后）。worktree 分支
> `feat/pi-harness-boundary-convergence`。Subagent A（只读勘察）因限流失败，
> 本 recon 由主 agent 亲自完成全部读取。

## 1. 当前实现（master 事实）

### 1.1 Bridge 文件结构（app/agent_pi_bridge.py，3294 行）

| 区段 | 行范围（master） | 职责 |
|---|---|---|
| 常量/超时/turn_id 铸造 | 1–147 | `USE_NEW_AGENT`、drain/stall/total 预算、`_inject_turn_id` |
| PiToolRequest/Response 模型 | 149–171 | Pi HTTP 回调契约 |
| Tool registry 注入 | 174–242 | `set/get/try_get_tool_registry` + shared `ToolDispatchService` |
| Dispatch 结果缓存 + 三类缓存 | 201–380 | `_dispatch_result_cache` / `_session_plan_sse_cache` / `_session_executed_sets`（ADR-0022 rendezvous） |
| `_slim_pi_details_payload` | 383–418 | 64KB details 上限 + ref 化 |
| `dispatch_tool` / `_dispatch_tool_bound` | 446–1289 | **~840 行 GIS-specific 调度胶水（本方向主拆解对象）** |
| no-progress 诊断 + hard stop | 1292–1386 | `_record_gis_progress` / `_hard_stop_pi_turn_for_no_progress` |
| Active-turn 表（V5-B） | 1397–1500 | session→turn 注册表 + `__getattr__` back-compat |
| `PiBridge.prompt`（非流式） | 1967–2320 | ~355 行 |
| `PiBridge.stream_prompt`（流式） | 2322–2975 | ~650 行 |
| 池/生命周期 | 2978–3100 | `get_pi_bridge` / `PiBridgePool` / shutdown |
| `SwarmBridge` | 3104–3292 | 委派网关（守卫序清晰，不动） |

### 1.2 `_dispatch_tool_bound` 内的阶段链（顺序即权威序）

1. **面解析**：`resolve_pi_tool_call` + `registered_surface_names`（reject → typed 拒绝 + 指标）
2. **SessionPlan slot + kernel `begin_step`**（host="pi"）
3. **存在性 + tier>=3 拒绝**（Pi 特有安全守卫）
4. **Capability dispatch bind**（#1395 新增，`check_tool_capability_at_dispatch`，default ON，fail-open）
5. **Input gate**（schema 预校验，typed `schema_validation_rejected`，含 plan 行标 failed 记账 + 锁重试）
6. **surface 指标**（record_surface_call）
7. **真实 dispatch**：`ToolDispatchService.dispatch`，包 `use_token` + `use_origin(JobOrigin)`
8. **取消/异常分支**：harness `ToolCallEvent` + tracker cancelled step
9. **TaskTracker step 记录**（迟到回调守卫 #993/#1069）
10. **cache_dispatch_result**
11. **ok 分支**：`apply_tool_evidence`（锁重试）→ `maybe_finalize_map_product` → `maybe_update_workflow_instance` → `maybe_update_runtime_state` → `maybe_update_runtime_projection` → map_finalization SSE 缓存
12. **error 分支**：`apply_tool_evidence(success=False)`（锁重试）→ `workflow_instance` → `runtime_projection`（**无 runtime_state_machine 推进——ok/error 不对称**）
13. **cartography 证据**：`_persist_cartographic_harness_context`（stale generation → 诚实 short-circuit 返回）+ `record_map_action_issued` + `record_event` + `evaluate_cartographic_session`
14. **gis_trace 阶段**（TOOL_CALLS/ARGUMENTS/TOOL_RESULTS/MAP_MUTATIONS）
15. **no-progress 诊断 + hard stop**
16. 组装 `PiToolResponse`（details 脱脂）

重复形态：`apply_tool_evidence` 成功/失败两分支几乎逐行相同（含 TimeoutError 锁重试，2×~55 行）；`workflow_instance`/`runtime_projection` 推进在 ok/error 重复；`try/except + logger.debug` 增值披露样板 ≥10 处。

### 1.3 Stream vs Non-stream 分叉清单（实读对比 prompt vs stream_prompt）

| # | 项 | stream_prompt | prompt（非流式） | 定性 |
|---|---|---|---|---|
| D1 | process_died 时 tracker 结算 | `fail_task` | **`complete_task`**（bug：同函数内 kernel 状态映射含 process_died→failed，tracker 却落 completed） | **P1 修复** |
| D2 | turn-settle 投影推进（workflow_instance/runtime_state/runtime_projection） | agent_settled 时执行 | **缺失**（路由层仅补 `maybe_finalize_map_product`，chat.py:926-947） | **P1 修复**（收敛进共享 settle 管线） |
| D3 | turn-settle 证据链 USER_OUTPUT + `persist_turn_chain` | 有 | **缺失** | **P1 修复** |
| D4 | turn-settle `checkpoint_context_layers`（受 GIS_RUNTIME_STATE_MACHINE 门） | 有 | **缺失** | **P1 修复** |
| D5 | `rt_ev.mark_first_event()` | 每个 real 事件 | 缺失 | P2 修复（可观测性） |
| D6 | 超时 failure_class | `pi_turn_budget` / `pi_stall` 两分 | 单一 `drain_timeout` | P2 对齐（非流式 drain 同样能区分 stall/total 预算） |
| D7 | `maybe_record_turn` 载荷 | 含 map_product | 无 map_product | P2（随 D2 的 settle 管线顺带修复） |
| D8 | task_start SSE / map_finalization SSE | 有 | 无（协议形态差异，合理） | 保留（documented） |
| D9 | `on_turn_result` sink | 有 | 无 | 保留（非流式直接返回 dict） |

### 1.4 Feature flag 盘点（harness hotpath）

**Default ON（kill switch 保留）**：`GIS_CAPABILITY_DISPATCH_BIND`、`GIS_CLAIM_INGEST`、`GIS_CONTEXT_POLICY`、`GIS_TOOL_RETRIEVAL_V4`、`GIS_TOOL_RETRIEVAL_V6`、`GIS_TOOL_SEMANTIC`（空=off 待核）、`GIS_CAPABILITY_RETRIEVAL_V7`、`GIS_CAPABILITY_GRAPH_V8`、`GIS_CAPABILITY_PLANNING_V1`、`GIS_RECOVERY_LEDGER`、`GIS_WORKFLOW_RUNTIME_V6`、`GIS_RUNTIME_STATE_MACHINE`、`GIS_TRACE_PERSIST`、`GIS_TRACE_COMPRESS`、`GIS_WORKFLOW_INSTANCE`、`USE_NEW_AGENT`（config）。

**Opt-in（default OFF）**：`GIS_MISSION_HOTPATH`（+`GIS_MISSION_RUNTIME` 双闸，刻意）、`GIS_HARNESS_DELEGATION`、`GIS_SWARM_ORCHESTRATOR`、`GIS_VISUAL_EVALUATOR`、`GIS_TRACE_FSYNC`。

**病灶**：consult 点散布 14+ 文件，无单一 registry；`hotpath_convergence/flags.py` 只覆盖 3 个；同一 flag 多文件重复 `_env_truthy` 实现；无「flag 咨询点 ↔ registry」一致性测试 → flag 矩阵漂移无法被 CI 捕捉。

### 1.5 最近已合并、**不得重做** 的工作

- **#1477（28a2f73e）**：capability dispatch bind（`capability_bind.py` typed decision）、mission bind 上 Pi 双路径（`pi_mission.py`，chat 路由调用）、project knowledge 注入（context_assembler）。→ **capability/mission 注入不碰**。
- **#1471（e9ea57c1/#1407）**：kernel lifecycle、迟到回调 plan-evidence 守卫（`_late_for_plan` gate）。→ **迟到回调对 plan 证据的隔离已有，不重做**。
- **#1470**：capability graph registry；**#1472**：governor dispatch gaps；**#1468**：GDAL redirect；**#1402/#1401** 已在 master。
- ADR-0022 约束：`_dispatch_result_cache` + 双适配器是「刻意 rendezvous」，**留在 bridge**（模块 docstring 明示），拆解不得移动缓存本体。

### 1.6 Bridge 外部契约（必须保持兼容的符号）

生产消费者：`pi_tools.py`（`PiToolRequest/Response/dispatch_tool/is_active_pi_turn`）、`main.py`（`set_tool_registry/USE_NEW_AGENT/get_pi_bridge/shutdown_pi_bridge`）、`chat.py`（`PiRpcError/USE_NEW_AGENT/get_bridge_pool/evaluate_cartographic_session/is_cartographic_session_deleted/clear+restore_cartographic_session_state`）、`session_cancellation.py`（`get_active_turn_entry`）、`pi_native_surface.py`+`tool_surface_v3.py`+`plan_orchestrator.py`+`dispatcher.py`+`network_dependency.py`（`get_tool_registry`）、`capability_graph.py`（`try_get_tool_registry`）、`health.py`（`get_bridge_pool`）。

测试深度依赖内部符号：`_dispatch_tool_bound`、`_session_executed_sets`、`cache_dispatch_result`、`take_session_plan_sse`、cartography re-exports（`_get_session_harness` 等）→ **拆解必须保留模块级别名**。

### 1.7 测试现状

Bridge 相关测试 ≥30 文件（`test_pi_bridge_lock.py`、`tests/unit/test_pi_bridge_concurrency.py`、`test_pi_dispatch_adapters.py`、`test_pi_dispatch_cache_eviction.py`、`test_pi_session_plan_host.py`、`test_runtime_chaos_pi.py`、`test_streaming_lifecycle.py`、`test_harness_kernel_e2e_scenarios.py`、`test_audit_1395_decision_chain.py` 等）。**无专门的 stream/non-stream parity 测试文件**（散落在 lifecycle/kernel e2e）。

## 2. 本方向真实缺口（结论）

1. **后置披露管线无形化**：~840 行内联胶水，无 typed 契约；ok/error 双分支重复；「增值披露绝不阻断」纪律靠 10+ 处复制粘贴样板维持。
2. **Stream/non-stream parity 靠路由层补丁**（finalization 在 chat.py 手工补），turn-settle 投影/链持久化/checkpoint 只在流式存在；process_died tracker 结算错误。
3. **Flag 面无单一真相**：registry 缺失，默认值/kill-switch 状态无一致性测试。
4. 无 Pi/Harness ownership 的正式 ADR（边界规则散落注释）。

## 3. 禁止事项（自我约束）

- 不动 ToolDispatchService 契约（dispatch 回答「发生了什么」，调用方负责「如何披露」——本 PR 正是把后者形化，不是移动边界）。
- 不动 ADR-0022 缓存 rendezvous 位置。
- 不重做 #1395 capability bind / #1407 late-callback guard / mission bind。
- 不新增第二套 agent loop / 不 fork Pi / 不搬 ToolDispatch 进 Harness。
- 不做大规模格式化；bridge 行为语义除 D1–D7 修复外逐字节保持。
