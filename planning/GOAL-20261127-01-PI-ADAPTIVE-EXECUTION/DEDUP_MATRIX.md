# DEDUP_MATRIX — GOAL-20261127-01

> 对照对象：#2276/#2280（Pi v1.6.1 宿主融合）、#2283（决策治理）、#2284（全量审查）、
> #2288（整合）。状态：present / partial / missing / covered-by-other / blocked。
> 证据 = path:line（master @ f34cb69dd）。

## A. 任务书工作包 vs 现有实现

| 工作包能力 | 状态 | 现有实现证据 | 剩余增量（本线） |
|---|---|---|---|
| Pi 原生宿主 + RPC 桥 | **present**（#2280/#2276） | `pi_rpc_client.py`（spawn/RPC 复用/有序停机）、`pi_event_mapper.py`、`pi_compat.py` 能力快照、`pi_doctor.py` 六域 | 无（只消费） |
| 模型路由权威（硬资格+健康护栏） | **present**（ChatEngine 面） | `model_runtime/routing.py:84-180`（capability_skip/health_skip/degraded_chain） | **Pi 路径 missing**（见 B1） |
| 会话模型目录+偏好 CAS+能力门+预算 admission | **present**（#2276 系 G37） | `session_catalog.py`、`session_model_prefs.py`（CAS revision）、`session_model_service.py:100-444`（resolve/gate/admission/apply+回读） | 与路由权威的**健康降级接线**（B1）；UI budget 投影（B6） |
| usage 持久账本（Pi 主链） | **present** | `usage_ledger.py`（(turn_id,call_seq) 幂等、cost provider/estimated/unknown、ChatTurnRuntime/ChatUsageRecord） | attempt/route 关联列（B3） |
| provider 失败观测（vendor 事件） | **partial**（#2283 系 ADR-0301 D5） | `index.mjs:607-618`（stderr 有界环）、`pi_event_mapper.py:447-497`（auto_retry note+诊断） | **健康表回写 + attempt 链**（B2/B3） |
| 失败分类器 | **present** | `model_runtime/provider.py:20-95`（FailureKind 10 值 + classify_status/exception） | 复用，不重建 |
| 错误体消毒 | **present** | `provider.py:172-186`（sanitize_provider_error） | 复用 |
| governor（预算/准入/重试/背压/取消） | **present** | `governor/*`（session_budget 四级链、admission 五值、retry_budget 令牌、backpressure 通道、cancellation 四硬保证）；dispatch 唯一强集成点 `tool_dispatch_service.py:1082-1106` | 真实 usage 回流缺口（B4）；不重建 |
| durable job + turn 撤销 | **present** | `jobs/*`（store/submit/context JobOrigin/lifecycle）、`pi_durable_cancel.py` | job 完成→模型续跑恢复点 missing（B5） |
| SSE resume | **present**（只重放） | `event_resume.py:4-7`「never start another execution」 | 续跑义务投影走 UI 面（B5），不改语义 |
| 有界决策辅助（decide_task） | **present**（#2283） | `pi_decision.py`/`pi_decision_wiring.py`（transport 注入点、tie/specificity 闸） | 无（回归保护） |
| schema 卫生 / tool_search / codemode / compaction 治理 | **present** | #2283 交付 + `pi_surface_receipt.py`、`index.mjs:591-600` | 无（回归保护） |
| turn 上下文装配回执 | **present** | `pi_turn_context.py`、TurnContextReceiptRow（assembled/not_sent/submitted/unknown） | 无 |
| 会话 usage API | **partial** | `session_model_views.py:255`（GET /usage → session_usage_summary） | **budget 块缺失**（B6：前端读 `usage.budget?.token_cap` 恒 undefined） |

## B. 本线增量缺口清单（与 PLAN.md G1–G4 对齐，证据见子代理审计）

| # | 缺口 | 级别 | 证据 | 处置 |
|---|---|---|---|---|
| B1 | Pi inherit 路径不经 ModelRouter（健康冷却→无降级链、无 reason codes 留痕）；用户显式选择行为不变 | P1 核心 | `agent_pi_bridge.py:1686`→`apply_session_model`（无 router 调用）；`RouteRequest.prefer_model` 注释（routing.py:69）预期未兑现 | S2：strategy 接线（候选=管理员目录 ∩ 路由护栏） |
| B2 | Pi provider 失败不回写 LLMProviderHealth（健康表只被 ChatEngine observe 喂） | P1 核心 | `execution_engine.py:1050/1268/1302` 是唯一 record 路径；Pi 面无 observe | S2：auto_retry/turn 终态→classify→record_failure（复用 provider.py 分类器） |
| B3 | 无 attempt 链持久关联：auto_retry 计数、路由 reason codes、失败分类不进 ChatTurnRuntime；路由决策无持久 receipt（`route_receipt` 概念不存在） | P1 | `ChatTurnRuntime` 列面（db_model.py:1052-1104）无 attempt/route 字段；trace stage 无 DB 行 | S3：additive migration + 有界字段 |
| B4 | governor 从不收真实 usage/费用（只有组装估算）；`ESTIMATED_LLM_COST` 无生产写入 | P2 | `context_assembler.py:893-898`、`governor_link.py:188-192`；estimation.py:41-42 仅展示先验 | S3：turn 结算回流有界记账（CONTEXT/OUTPUT tokens 实数） |
| B5 | job 完成→模型续跑无恢复点（负向 grep 证实）；SSE resume 只重放不重启 | P2 | `event_resume.py:6-7` 声明；无 watcher/continuation | S3：诚实投影 + 用户显式「继续」操作（复用或新任务），不建 watcher 自动重启 |
| B6 | `GET /usage` 无 budget 块，前端 `session-model-selector.tsx:240` 读 `usage.budget?.token_cap` 恒 undefined | P2（真实契约缺陷） | `usage_ledger.py:477-527` 返回字段 vs 前端读取 | S4：usage 投影补 effective cap/remaining（与 admission 同一权威） |
| B7 | 工具内部 LLM（spatial_reasoning/intent_semantic/prescriptive_advisor/VLM）绕过路由/健康/账本 | P2（范围裁剪：本线只做观测面接线，不改工具语义） | `tools/spatial_reasoning.py:230-231` 等直连 resolve_llm_config | S2 部分：失败经 observe 上报；完整改造超出本线边界（记录） |
| B8 | thinking 档硬编码不可控（`_thinking_effort_supported()` 恒 False） | 记录不动 | `session_model_service.py:447-455`（当前端点契约） | 不动（诚实披露） |
| B9 | 双 session token 预算口径并存（账本实数 vs governor 估算） | duplicate-risk | `session_model_service.py:200-273` vs `session_budget.py:260-280` | S3 决策：admission 以账本实数为准（现状即如此）；governor 面定位资源协调；回流后口径收敛说明 |
| B10 | 重试计数三面（governor RetryBudget / AnalysisTask.attempt / profile.max_attempts） | duplicate-risk | retry_budget.py:13-15 自述唯一消费方是 dispatch 面 | 不合并（各自语义不同）；投影面分列披露 |

## C. 明确不做（避免抢占/重复）

- 不写新 router/governor/日志库/classifier/schema sanitizer/compaction 系统。
- 不改 vendor/pi（上游最新 tag=v1.6.1=pin，ls-remote 实证）。
- 不做 02–05 线领域工具（轨迹/抽样/发布/工程包）。
- 不机械开启全部 Pi 上游功能（能力快照词表封闭）。
- 不动 ChatEngine 现有路由消费（回归保护）。

## D. 覆盖归属

- **covered-by-other**：#2284 已做全量逐行修复（当前主 worktree 分支已合并）；
  #2289 会话文件管理（与本线无关）；02–05 线领域交付（各自 PR）。
- **blocked**：真实商用 provider 凭据缺失 → 真实 LLM 付费调用不可测（沿用
  #2283 披露口径：mock HTTP provider + 真实 vendored RPC；本地模型无可配置
  凭据，同列未实测）。
