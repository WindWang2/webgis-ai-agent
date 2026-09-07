# Trace / Replay / 可靠性语料

## Trace 事件模式（§33）

`app/lib/runtime/trace.py` —— 进程内观测面，**不落盘**（落盘仍由 tool_metrics
JSONL 与 decision_log 负责，不建第二持久化真相）。

17 种封闭事件：`turn_start / model_selected / context_built /
tool_surface_selected / model_request / tool_call_proposed / tool_args_normalized /
dispatch_started / dispatch_completed / artifact_produced / plan_progressed /
map_product_changed / no_progress_detected / fallback / subagent_spawned /
subagent_completed / turn_settled`。

**载荷策略（显式）**：键名命中 key/token/secret/password/authorization/api →
`[REDACTED]`；字符串值 >512B 摘要化；list/dict 以 `<type len=n>` 表示；每 turn
事件环 ≤256（超出丢最旧并记 dropped）；turn 注册表 LRU ≤128。

已接线（全部异常安全，观测绝不阻断执行）：
- pipeline：`tool_call_proposed` / `dispatch_started` / `dispatch_completed`
  （状态+耗时）/ `tool_args_normalized`（修复证据计数与种类）；
- 引擎 `_augment_tool_surface`：`tool_surface_selected`（tools/bytes/fingerprint）；
- router.observe：失败 → `fallback`。

## Replay harness（§34）

`app/evaluation/replay.py` —— 三种确定性模式（无 LLM / 无网络）：

1. **`replay_tools(registry, session, entries)`**：重放声明 `replay_safe` 的
   工具；destructive / external_side_effect **永不自动执行**（`confirm_tier3`
   参数也只是前向兼容，当前永不授予）；以 `ToolResultView.contract_key()`
   做形状比较（非 bytes diff）。
2. **`simulate_agent_loop(registry, session, script)`**：脚本化模型输出
   （`ScriptedCall`：工具 + 实参 + 期望错误码/结果 + 期望 no-progress 原因），
   走真实 dispatch（归一化/校验/tier-3 闸/修复证据全真），校验运行时不变量：
   破坏性工具无确认必拒、期望必命中、模式检测联动（读/突变类别由描述符声明）。
3. **`check_trace_invariants(trace)`**：状态机性质（dispatch_completed 必有
   started 前驱、turn_settled 至多一次、fallback 前必有 model_selected、
   settle 时无在途 dispatch）。

## 可靠性语料（§37）

`app/evaluation/reliability_corpus.py` —— `build_reliability_corpus()` 程序化
生成 19 类确定性场景（单/多工具、依赖链、并行、坏参数自愈、别名、缺 ref、
大结果、重复失败、反复读、突变无状态变化、破坏性拒绝、别名绕过、取消分类、
工件、JSON-串列表、kebab、NaN 注入……）。场景 id 稳定；断言只锁运行时不变量
不锁文案。执行方：`tests/unit/test_reliability_security_perf_v2.py`
（全量门 + 确定性重放门）。规模按类别扩展：向 `build_reliability_corpus`
追加 `_c(...)` 即可，主门零改动。

## 大注册表可扩展性（§43/§44）

同文件参数化测试：200/500/1000 合成描述符下注册 / 清单生成 / 全库指纹 /
检索（冷+热索引）/ 策略审计全部有界（松散预算，CI 噪音友好）。检索索引按
`registry_fingerprint` 缓存 —— 1000 工具热检索走 O(tokens) 词法匹配 +
指纹命中短路。

## V3 证据链 / A-B 重放 / 指标（ADR-0103）

### 18 阶段证据链

`app/lib/runtime/gis_trace.py` —— TurnTrace（turn 内事件流）之上的**证据
链**层：`Stage` IntEnum 规范 18 阶段，值即规范序 —— USER_INTENT →
PARSED_INTENT → TASK_ONTOLOGY → DATA_PROFILE → CANDIDATE_WORKFLOWS →
SELECTED_WORKFLOW → TOOL_SURFACE → MODEL_ROUTING → TOOL_CALLS → ARGUMENTS →
TOOL_RESULTS → ARTIFACT_CREATION → MAP_MUTATIONS → MAP_OBSERVATION →
VERIFICATION → REPAIR → FINAL_VERDICT → USER_OUTPUT。

- `GisTraceChain`：每阶段至多 8 条（超出丢最旧留最近），payload 过
  `bound_meta` 同源消毒（敏感键 REDACTED、大值摘要）；
- `GisTraceRegistry`：turn→chain LRU ≤128；`record_stage` 链不存在时惰性
  创建（发射侧零前置依赖），任何失败返回 False 绝不抛出；
- `completeness()`（覆盖阶段数 / 18）是评测指标；
- **已接线发射点**（本分支只接两处缝；intent/verdict 发射随各自引擎改动
  落地）：
  - model routing bridge（`_log_reasons`）：有活跃 turn 时记
    `MODEL_ROUTING`（role / model / reason_codes[:8] / fallback_chain[:8]）；
  - Pi dispatch（`app/agent_pi_bridge.py`）：每次真实 dispatch 后记
    `TOOL_CALLS`（tool+call_id）/ `ARGUMENTS`（原始长度钳 512B）/ 
    `TOOL_RESULTS`（status + latency_ms）/ `MAP_MUTATIONS`（action_id +
    command 各钳 8 条）—— 记录绝不阻断工具返回。

### Replay A/B（app/evaluation/replay.py）

全部确定性、零副作用（同输入、两配置、比投影；比较中绝不执行任何工具或
LLM）：

- `ab_compare_tool_surface(registry, message, ctx_overrides_a,
  ctx_overrides_b)` → `SurfaceABResult`（only_in_a / only_in_b /
  retriever_a/b / delta）；
- `compare_chains(chain_a, chain_b)` → `ChainComparison`（阶段覆盖差集 +
  双侧 completeness —— workflow regression 断言面）；
- `route_decision_diff(decision_a, decision_b)`（决策 dict 字段级差异，
  不含健康状态易变维度）。

### 五族指标（app/evaluation/runtime_metrics.py）

1. Tool retrieval（`retrieval_metrics`：recall@k / precision@k / irrelevant
   tool rate）；
2. Model routing（`routing_metrics`：fallback rate / failure rate / 平均
   时延）；
3. Agent execution（`execution_metrics`：completion / repeated calls /
   timeout / no-progress / 平均工具数）；
4. GIS correctness（`gis_correctness_metrics`：算法族正确率、数据资格、
   final map state 断言透传）；
5. Context（`context_metrics`：overflow / truncation / schema & result
   token 比）。

检索标注真相 = `AlgorithmRegistry.capability_tool_map` 反查 + golden case
`expected_capabilities`（`relevant_tools_for_case`，只取活注册表内
model-visible、tier<3）—— 不引入新的人工标注真相。

### GIS 无进展诊断（app/services/chat/no_progress.py）

`GisProgressTracker` 在形态级 reason codes 之上叠加真实状态停滞观测：
`unchanged_map:N`（mapspec 指纹连续 N 次成功调用纹丝不动，阈值 4）、
`unchanged_workflow:N`（SessionPlan capability 进度代数停滞，阈值 6）、
`repeated_planning:N`（规划签名重复出现，阈值 2）；失败调用不推进停滞计数。
Pi dispatch 路径每次真实 dispatch 后喂 tracker（per-session 有界 ≤64，只存
代数与签名不存内容），阈值命中以 `no_progress_hints` 附进
`PiToolResponse.details` —— 模型可读的诚实诊断，绝不改变工具结果本身；
调用方据此切换 fallback/repair。

### 上下文预算 V2（app/services/chat/context_budget.py）

新增四分区 `Category.DATA_PROFILE / ALGORITHM_METADATA /
CARTOGRAPHY_METADATA / TOOL_RESULTS`（12-15），确定性 caps 0.04 / 0.04 /
0.04 / 0.15。`GisBudgetAdvisor` 产出 per-component 处理**建议**（keep /
condense / offload_ref / drop_oldest / compress）+ 可观察报告 —— 只 advise
不 mutate prompt，执行权仍在既有组件；assembler 的 `budget_report` 携带
advice 块。

测试锚点：`tests/unit/test_gis_trace_v3.py`、
`tests/unit/test_runtime_metrics.py`、
`tests/unit/test_execution_stability_v3.py`（GisProgressTracker）、
`tests/unit/test_context_budget_gis_v2.py`。
