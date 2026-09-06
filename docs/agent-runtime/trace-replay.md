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
