# Recon — Pi-native GIS Harness Kernel + SessionPlan

- Branch: `harness/pi-native-kernel-sessionplan-v1`
- Baseline: `origin/master` = `580b33e9` (merge #1272, 2026-09-13)
- Date: 2026-09-13
- Method: 只读代码勘察 + 主 Agent 精读核心文件 + Subagent A 广度对账（PR review / gis_harness 盘点 / 测试基建）

## 0. 执行时仓库事实

- `origin/master` @ `580b33e9`：AC-V11（ADR-0160~0169）与 ADS-V1（ADR-0170~0179）两条主线已合并。
- Open PR：仅 `#1270 fix(ci): adaptive-wave hygiene + mapspec CLI alias`（碰 `mapspec/coordinator.py`、`frontend/lib/mapspec-compiler/compiler.ts`、`tests/conftest.py` 等）——CI/hygiene 线，**不吞入本任务**；仅当其改动与本分支冲突时做最小兼容并在 PR 归因。
- `harness/pi-typed-tool-surface-v1` 分支存在但 **0 commits ahead**（方向 4 的预留 worktree，无在途代码，无重叠）。
- 最近 merged：#1272 (ads-v1)、#1271 (ac-v11 M1)、#1269 (quality baseline)、#1263-1268 (adaptive cartography W1-W9)。全部集中在 cartography/data-supply，**无一触及 session/plan/turn 语义**。
- ADR 最高编号：**0179**。本任务新 ADR 从 **0180** 起占号（已检查 origin 分支，无更高在途占号）。

## 1. 任务书假设 → 实际代码（重要修正）

| 任务书假设 | 实际代码事实 | 调整 |
| --- | --- | --- |
| 「是否已经存在 SessionPlan/等价对象」待验证 | **已存在**：`app/services/session_plan.py`（853 行，ADR-0076），Pi 路径 host-plan envelope，Redis 持久化 + 3 条 SSE 事件 + 前端 hydrate-then-delta 面板 | **不新建第五套 plan class**；在既有 SessionPlan 上 additive 扩展契约 |
| 「plan SSE 仍由 legacy 独占」 | 部分成立：legacy `plan_ready/plan_step_done/plan_finalized` 仍 legacy-only；但 `session_plan_updated/progress/superseded` 已在 Pi 路径流动且前端已消费（#1047/#1048） | K6 聚焦：step 级证据 + legacy plan_* HUD 与 session_plan_* 语义统一映射 |
| 「Pi 与 ChatEngine 仍有不同 plan evidence」 | 成立：legacy=CanonicalPlan(`planning/models.py`)+decision_log；Pi=SessionPlan envelope；互不读写 | K4 adapter 让 legacy 经同一契约写入 |
| 「GIS session state 仍散落在 bridge」 | 部分成立：SessionPlan 逻辑已是中性 service；但 **apply_tool_result 的重试/锁竞争处理、SSE 缓存 rendezvous、executed-sets、进度 tracker** 仍寄居 `agent_pi_bridge.py` | K1 把通用 GIS session 生命周期上收 GISSessionRuntime；Pi-only RPC/cache 留 Pi 域 |
| 「Pi 是否仍跳过 CanonicalPlan」 | 成立：`chat.py:965-970` 明确注释 + `_maybe_plan` 仅 legacy 调用 | 维持：不把 legacy planner 链移植到 Pi |

## 2. 生产调用链（before）

```text
                         ┌─────────────────────────── Pi host（默认, USE_NEW_AGENT=1）───────────────────────────┐
POST /chat (chat.py:852) │  _use_pi_bridge() → pi_event_generator (chat.py:986)                                  │
                         │    ├ _record_frontend_cartographic_observation   [map_state 写]                       │
                         │    ├ _build_cartography_turn_context             [verdict/memory 块]                  │
                         │    └ turn_bridge.stream_prompt (agent_pi_bridge.py)                                   │
                         │        ├ bind_turn_prompt (pi_turn_context.py:210)                                    │
                         │        │   ├ ensure_session_plan_slot + load_session_plan   [hydrate]                 │
                         │        │   ├ format_session_plan_projection → plan_block    [上下文注入]              │
                         │        │   ├ compile_tool_surface + active_tools marker                                │
                         │        │   └ v6 三层块 + evicted-refs tombstone                                        │
                         │        ├ register_active_pi_turn（_active_turns 表 + turn token HMAC）                 │
                         │        └ Pi RPC 循环（pi_rpc_client, --no-session 子进程）                             │
                         │            每个工具回调 → HTTP /pi-tools/execute → dispatch_tool (bridge:396)          │
                         │                ├ ensure_session_plan_slot (bridge:516)                                 │
                         │                ├ ToolDispatchService.dispatch（共享执行真相，tier 门，repeat 拦截）     │
                         │                ├ apply_tool_result（session_plan.py:541，会话锁内）                     │
                         │                │   ├ webgis_map_intent → supersede/replace（goal_key 判同）            │
                         │                │   ├ webgis_map_product → merge + progress 打勾                        │
                         │                │   └ 其他工具 → capabilities_hit_by_tool 打勾/标 failed                    │
                         │                ├ cache_session_plan_sse → (session,toolCallId) 缓存                    │
                         │                ├ maybe_finalize_map_product / workflow_instance / runtime_state /      │
                         │                │   runtime_bridge 四个增值推进点（bridge:757-836）                      │
                         │                └ pi_event_mapper: tool_execution_end → take_session_plan_sse flush     │
                         │    finally: buffer.mark_ended / _persist_pi_transcript（chat.py:990，DB transcript）   │
                         └────────────────────────────────────────────────────────────────────────────────────────┘
                         ┌─────────────────────────── Legacy host（USE_NEW_AGENT=0）─────────────────────────────┐
POST /chat               │  event_generator → ChatEngine.chat_stream (execution_engine.py:1778)                   │
                         │    ├ _maybe_plan (1068)：classify_followup → plan_orchestrator.orchestrate_plan        │
                         │    │   → CanonicalPlan 经 plan_store（plan-current 别名 + revision guard）             │
                         │    ├ plan_ready SSE (1933) / plan_step_done (2316) / plan_finalized (1961)             │
                         │    ├ tool_pipeline → 同一 ToolDispatchService（不写 SessionPlan）                       │
                         │    └ decision_log（ChatEngine-only）                                                    │
                         └────────────────────────────────────────────────────────────────────────────────────────┘
Shared 底座：ToolRegistry + ToolDispatchService（ADR-0006）、cartography_runtime（ADR-0071）、
session_data_manager（refs/aliases/map_state）、MapSpec store/checkpoint、artifact_registry、
session_lock_registry（fail-closed 分布式锁）。
```

## 3. 既有抽象盘点（owner / producer / consumer / 持久化 / 事件）

| 抽象 | 位置 | Producer | Consumer | 持久化 | 事件 | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| **SessionPlan envelope** | `app/services/session_plan.py` | Pi bridge（apply_tool_result / ensure_slot）、map_intent 工具结果 | Pi turn context 投影、GET /sessions/{id}/plan、前端 SessionPlanPanel、artifact_registry、workflow_engine、analysis_graph、context_layers、no_progress tracker | session_data_manager，alias `session-plan`，历史 `session-plan-id:<envelope_id>` | `session_plan_updated/progress/superseded`（Pi 缓存 rendezvous flush） | **保留并扩展为本任务契约载体** |
| **CanonicalPlan** | `app/services/planning/models.py` + `store.py` | legacy `_maybe_plan` → plan_orchestrator | legacy 执行引擎（步骤打勾/plan_* SSE）、decision_log、PlanStore LRU | session_data_manager，alias `plan-current` + `plan-id:*`，revision guard | `plan_ready/plan_step_done/plan_finalized`（legacy SSE） | **保留；K4 加 SessionPlan 投影 adapter，标 deprecated 的只是"独占状态"** |
| Orchestrator `Plan` | `app/services/chat/plan_orchestrator.py` | legacy planner LLM | execution_engine | （CanonicalPlan 投影） | — | 保留（legacy 域） |
| CapabilityProgress 行 | session_plan.py | apply_tool_result | 投影/前端/SSE | 同 envelope | session_plan_progress | 保留（step 语义承载于 gis_chapter 行 + 本任务 PlanStep 扩展） |
| plan_graph DAG 投影 | `gis_harness/plan_graph.py` | 纯派生自 gis_chapter | 投影行 / action_intent | 无（纯函数） | — | 保留（不做第二 ExecutionGraph） |
| runtime_state_machine / plan_runtime / runtime_bridge / workflow_instance | gis_harness | bridge 四个推进点 | 投影行 | gis_chapter 内嵌块 | — | 保留；本任务 checkpoint/recovery 与其**互锁不重写** |
| decision_log | `chat/decision_log.py` | legacy 引擎 | 审计 | DB/log | — | K1 起双 host 经 runtime 记 decision journal（wire 层各自兼容） |
| TurnEventBuffer + resume | `chat/event_resume.py` + chat.py `_resume_generator` | chat.py | SSE resume (Last-Event-ID) | 进程内存 ring | 全部事件缓冲 | 保留（K6/K7 复用，不建第二 stream） |
| mapspec_checkpoint_store | `mapspec_checkpoint_store.py` | cartography_runtime | MapSpec 恢复 | 独立 store | — | 保留（K7 引用其 checkpoint_id 语义） |
| pi_turn_registry / _active_turns / dispatch cache | bridge + pi_turn_context | Pi host | token/cancel/correlation | 进程 + Redis（active_turn） | — | **留 Pi 域**（K1 不迁） |

## 4. 与最近 PR 的重叠矩阵

| 能力面 | #1263-1269 (AC W1-W9) | #1271 (AC-V11) | #1272 (ADS-V1) | #1270 (open, CI) | 本任务 |
| --- | --- | --- | --- | --- | --- |
| session/plan/turn 生命周期 | 无 | 无 | 无 | 无 | **主战场** |
| SessionPlan 契约 | 无 | 无 | 无 | 无 | additive 扩展 |
| cartography quality/symbology | 全量 | 全量 | — | conftest env pin | 不碰 |
| data supply/retrieval | — | — | 全量 | — | 不碰 |
| MapSpec CLI/compiler | — | — | — | compiler.ts 相对 import | 仅冲突时兼容 |
| tests/conftest env baseline | — | — | — | CARTO_METRICS pin | 新增 env 变量时同步 `_ENV_BASELINE`（沿用其模式，非吞并） |

结论：**零功能重叠**；文件冲突面集中在 `tests/conftest.py`（env baseline 追加）与 `agent_pi_bridge.py`/`chat.py`（无人近期改动，#1270 未触及）。

## 5. 复用 / 扩展 / 不做清单

**复用**：SessionPlan store + alias 机制；session_lock_registry fail-closed 锁；`events_to_sse` 禁止 CanonicalPlan 事件名的守卫；TurnEventBuffer/resume；PlanStore revision guard 模式（移植为 SessionPlan CAS）；capability registry / algorithm registry 的 tool→capability 映射；既有 metrics 载体（tool_metrics / cartography_metrics_store 模式）。

**扩展**：SessionPlan 契约 additive 字段（revision/created_at/turns/steps/decisions/recovery）；apply_tool_result 生命周期上收 runtime；Pi turn begin/end 接线；legacy → SessionPlan 投影 adapter；SSE 增加 step 级增量事件（additive 事件名）。

**删除/重复防范**：不建新 plan class、新 store、新 stream、新 metrics 框架、新 agent loop。`session_plan.py` 的旧 `SESSION_PLAN_PROGRESS` 等事件名一律保留（前端契约冻结）。

**不做**：Capability Graph（方向 3）、typed tool surface（方向 4，另有空分支）、ExecutionGraph scheduler（方向 5）、cartography/data-supply 重实现。

## 6. Subagent A 对账结论（PR review / gis_harness / 测试基建）

- **PR review**：#1270 body 自述"Does not address review P1/P2 (JWT TTL, dual visual-judge, ADR status, README)"——该批 P1/P2 来自制图 wave 离线 review，GitHub 无 inline 记录，**不属本任务**。#1271 的 svg2pdf 类型声明/Postgres dialect 阻塞已由 ecfe5961 修复。#1272 声明与 V11"冻结 slot 契约（互不 import）"，且 `app/services/gis_harness/**`、`frontend/**` 在该 PR 零触碰。#1272 门禁先例：`pytest tests/unit -q -n 2`。
- **Pi 无自有 step 结构**：SSE `step_start/step_result` 的 step_id = toolCallId（`pi_event_mapper.py:102-110`），非计划步骤；host 侧唯一计划对象是 SessionPlan（capability 粒度）→ kernel PlanStep 物化自 gis_chapter 行是净增量。
- **bridge 四连状态触发重复两处**：`agent_pi_bridge.py:757-820`（dispatch 侧）与 `:2264-2366`（agent_settled 侧）近乎重复的 finalize→workflow_instance→runtime_state→runtime_bridge 序列——kernel post-tool progression 的形状。
- **前端类型冻结**：`frontend/lib/types/session-plan.ts:6-10` 明文"两个计划概念（agent-plan vs session-plan），类型永不合并、字段永不互赋（ADR-0076）"——K6 只能扩 session-plan 家族。
- **无生产 importer 的实验模块**（勿误并）：goal_graph / candidate_planner_v8 / resume_verify / durable_context / continuation / observation_states / map_critique；`mapspec_checkpoint_store.py` 是 2 行 re-export 壳（真身 `app/services/mapspec/checkpoint.py`）；`app/lib/harness/pi_agent_harness.py` 是评估 harness 非运行时。
- **测试基建**：`tests/fixtures/pi_mocks.py`（播种式 mock RPC，无 LLM 全链路 E2E 先例 `test_pi_e2e.py`）；`tests/unit/test_session_plan.py`、`test_chat_session_plan_route.py`、`test_pi_session_plan_host.py` 是本任务测试基座；conftest `_ENV_BASELINE` 需 pin 新增 env；xdist 可用。
- **metrics 挂点**：tool_metrics JSONL（ADR-0044）、decision_log（已有 plan_id/plan_revision/step_id 字段）、TurnEvidence（TTFT/outcome）；缺口 = SessionPlan apply 失败只 log 无计数（K8 补）。
- **session 数据 TTL**：session_data 4h（Redis）/ LRU（内存）；SessionPlan 信封同域——kernel checkpoint 快照同 TTL，不另设持久层。

## 7. 已知风险

- `agent_pi_bridge.py`（2734 行）与 `execution_engine.py`（2795 行）是高流量生产文件：所有改动必须 additive + fallback 包裹，禁止改动既有分支语义。
- SessionPlan 前端契约冻结（session_plan.py 首行投影契约被测试锁定）：新字段只能 additive，不能改既有 SSE payload 形态。
- Windows 本地环境：Pi 子进程 spawn 依赖 Node/jiti；scoped 测试需 pin 既有 conftest env 基线。
