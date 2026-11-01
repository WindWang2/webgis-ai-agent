# BASELINE — GOAL 05 Harness 自主诊断、受控恢复与任务目标验收

基线：origin/master `6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（2026-10-31，PR #1875 合并后）。
勘察方式：3 个并行只读 Explore agents（失败分类/恢复模块域、执行闭环域、验收/评测域）+ 主 agent 交叉验证关键 seam 的源码行。

## 0. 三场景端到端链路（真实文件/函数锚点）

场景（成都小学可达性分析）自然语言 → `POST /chat`（app/api/routes/chat.py:2307）→
`_use_pi_bridge()`（chat.py:673，USE_NEW_AGENT=1 且桥存活）→ `PiBridge.stream_prompt`（app/agent_pi_bridge.py:2370）→
Pi 子进程 HTTP 回调 `POST /api/pi-tools/execute`（app/api/routes/pi_tools.py:53，三重门：bridge secret HMAC + turn token + live-turn 检查）→
`_dispatch_tool_bound`（agent_pi_bridge.py:513，守卫序：native surface → stale surface → plan slot → tier3 → schema 校验）→
`ToolDispatchService.dispatch`（app/services/tool_dispatch_service.py:308，**两引擎唯一 rendezvous**）→
capability bind（:400）→ 执行 → ref 铸造 + MapSpec 显示授权（`_author_display_result` :1513）→
`apply_post_dispatch_disclosure`（app/services/chat/pi_post_dispatch.py:80，SessionPlan 证据/终验/SSE）→
`maybe_finalize_map_product`（completion/pipeline.py:1040）→ `map_product_block`（:807，goal_satisfaction :917）→
task_complete SSE（pi_event_mapper.py:316）→ trace 链 settle 持久化（harness_kernel/settle.py:464）。
fallback 路径：ChatEngine `_stream_tool_wave`（execution_engine.py:2604）→ 同一 `ToolDispatchService.dispatch`。

## 1. 已有实现（逐子功能）

### 1.1 统一失败分类（failure_taxonomy.py，ADR-0248 D2）
- 入口：`classify_and_remediate(status/code/error_type/message/exception/tool_name/session_id)`（failure_taxonomy.py:321）→ dict payload `{class, remediation, attempts, max_attempts, retry_allowed}`。
- 11 类 `HarnessFailureClass`（:44）+ 8 种 `RemediationAction`（:32）+ `REMEDIATION_POLICY` 全表 max≤3（:61）。
- 记账：显式 ledger → durable `RecoveryLedger`（有 session）/ 进程级 `_global_ledger`；账本不可用 fail-closed → abort（:357-367）。
- 生产调用：**唯一 seam = tool_dispatch_service.py:1049**（统一错误分支，best_effort）。`result["harness_failure"]` 无任何程序化下游消费者（LLM 可读设计）。
- **缺口 A**：429/5xx/权限无一等公民词汇（429 折叠进 timeout、auth 折叠进 tool_error）；无 needs_user_data/needs_user_approval/unsafe_to_retry 处置分类；无 eligibility/no-progress/MapSpec 未收敛/goal 未达成的任务级归因。
- **存量 bug**：TDS:1060 记忆候选读 `_hf.get("failure_class")`，实际 payload 键为 `class` → 恒记 "unknown"。

### 1.2 修复计划（repair_planner.py，V6）
- 16 repair class + 5 safety class（含 requires_user_approval/not_allowed）；user-wins 锁/override → not_allowed（:241）；finding 指纹 + state_epoch 防循环（:253）。
- 生产调用：completion/pipeline.py:1154（终验块）→ `block["repair_plan"]`；消费方仅 runtime_state_machine._repairable_count（:230）。计划不直接执行（红线：执行归既有通道）。

### 1.3 运行时渲染修复（runtime_repair.py）
- `classify_runtime_repairs`（纯函数 :169）/ `run_runtime_repair`（:403）：revision 门、user-wins、执行债、W15 锁、轮数预算、ADR-0213 令牌闸、CAS 幂等（expected_revision）。
- 生产调用：api/routes/chat.py:2069（observation 上报端点，render_status==issues 且 finalizer 未修复时）。

### 1.4 Durable 恢复账本（recovery_ledger.py）
- session-plane JSON + flock；`record_failure/record_success/attempts/snapshot/copy_between_sessions`。
- 5 个生产消费点（failure_taxonomy:347、TDS:1478 record_success、resume_anchor:484 copy、candidate_planner_v8:159 penalty、capability_descriptors:296 可靠性反馈）。

### 1.5 项目级 Resume（resume_anchor.py + resume_verify.py，ADR-0248 D8）
- save/resume anchor 路由（api/routes/workflow_resume.py:47/:78，强鉴权 fail-closed）；ref 旧→新重写 + dangling 披露；verify_resume 五类核验；stale 行 → `workflow_v4.recompute.compute_affected_subgraph`。

### 1.6 目标满意度（goal_satisfaction/，ADR-0183）
- `GoalSatisfactionReport`（schema goal_satisfaction.v1）：requirement 6 kind / verdict 5 值 / signal 6 值 / evidence 11 kind×4 class；`PASS_CAPABLE_CLASSES={deterministic,structural}` fail-closed。
- **已接生产**（非 lab-only）：唯一评估点 `map_product_block`（pipeline.py:917-935）；SSE/task_complete/[GIS Goal] LLM 行/`signal==replan`→request_replan 四通道。
- 104 案例语料（false_pass_rate==0 门）。
- **缺口 B**：`repair_cartography/blocked_by_data/request_clarification` 信号仅披露不驱动；`DataQualification` blocked 的 reason_code/remediation 未投影进证据面（evidence.py:244-260 只看 roles[].status）；`observation_health` 被评估器显式忽略（goal_satisfaction_corpus.py:830）；L1-L5（pi_agent_harness._success_levels:1809）与 goal 报告无对账面。

### 1.7 分析工作流选择性重算（analysis_workflows/，PR #1869）
- AnalysisWorkflowContract + step 指纹（sha256(params+units+science+input identity)）+ downstream_closure 失效传播 + 生产工具 `webgis_analysis_workflow_recompute`（tools/analysis_workflow_tools.py:488）只重算受影响子图。receipt 红线：未执行步骤不得出现在 receipt。

### 1.8 执行闭环（TDS + Pi bridge + ChatEngine）
- 双引擎工具执行收敛于 `ToolDispatchService.dispatch`；错误回喂分叉：legacy `_classify_failure`（turn_recovery.py:43，**第三套 planning FailureClass 10 值**）进 step_error SSE；Pi 用 llm_payload 回子进程。
- TDS 错误分支（:1029-1122）：sanitize → harness_failure → 记忆候选 → tool_failed 事件 → trace 阶段 11 error → error result。
- 幂等/CAS：dedup executed_tools+_completed_keys（失败释放槽位）、SessionPlan revision CAS、MapSpec mutation expected_revision+CAS、observation fingerprint+generation+sequence 三尺。
- 鉴权：不在 TDS 内；路由层 require_owned_session / Pi 三重门（secret+turn token+live turn）。
- no-progress：Pi dispatch 级 streak 硬停（agent_pi_bridge.py:987-1001）；legacy 轮级 streak 诚实收尾（execution_engine.py:2548）。
- **缺口 C**：`harness_failure` 无程序化消费；Pi 与 legacy 恢复语义靠 LLM 文案而非统一裁决；legacy step_error 用第三套 planning FailureClass（turn_recovery.py:43）。
  - 勘察修正（主 agent 核实）：chain_emitters.py:2-5 "仅 5/18 阶段有生产发射" 的注释**过时** —— 阶段 14（chat.py:1951）/15（pipeline.py:57）/16（pipeline.py:64 finalizer 侧）/17（pipeline.py:70）/18（settle.py:465）均已存在发射点；runtime_repair 长期**没有**链发射（本任务补：runtime_repair.py:_trace_repair 增 Stage.REPAIR，与 finalizer 侧互补记录不同时刻的修复事件）。

### 1.9 可靠性回放（reliability_corpus.py + evaluation/replay.py）
- 三种 replay：replay_tools（allow-list）/ simulate_agent_loop（脚本化走真实 dispatch 校验不变量）/ check_trace_invariants；AB 比较。
- reliability corpus 19 案例（纯 ScriptedCall 序列，**无输入状态/允许动作/禁止动作字段**）；replay/faults.py 10 种故障 + FAULT_CONTRACTS。
- **缺口 D**：无恢复场景语料（mid-run 状态注入/恢复动作许可矩阵/回归验证）；G05 f13 跨会话恢复、faults 轨迹级、workflows 步骤级三层 case 结构互不共享。

### 1.10 G05 任务验收（tests/acceptance/gis_agent/ + docs/quality/g05-task-acceptance.md）
- ScenarioContract 21 场景（F01-F19）；判错归因九类（intent_wrong/wrong_dataset/invalid_method/unmet_product/stale_render/lost_interaction/export_parity/unsupported/governance_violation）；CheckVerdict pass/fail/not_evaluated。
- G05-F1/F2/F3 已知发现（CRS 无产品级接线已由 V5 修复 / not_in vacuous 已修 / 离线能力矩阵 network-only）。

## 2. 缺口 → 本任务增量（仅列有真实工具/用户场景可观察价值的）

| 缺口 | 证据 | 本任务对应 |
|---|---|---|
| A. 429/5xx/权限/eligibility/no-progress/MapSpec 未收敛/goal 差距无一等归因词汇与 6 类处置 | failure_taxonomy.py:44-73 词表封闭于工具错误；TDS:1049 无消费者 | M1 GISRecoveryDiagnostic.v1 |
| B. L1-L5 与 goal 证据无对账；observation_health/data_qualifications 不进裁决面 | goal_satisfaction_corpus.py:830；evidence.py:244-260 | M4 goal_verification（app/lib/harness/goal_verification/） |
| C. 恢复决策无副作用守卫的统一纯函数（风险分级×处置×预算） | REMEDIATION_POLICY 只有类→动作，无工具风险维度 | M2 RecoveryPolicy |
| D. Pi/legacy 恢复行为分叉、诊断不进 trace/计划投影 | turn_recovery.py 用第三套词表；chain_emitters 缺阶段 14/15/16 | M3 接线（TDS+pi_post_dispatch+step_error+投影行+trace payload） |
| E. 多轮"重试失败步骤/换数据源"无恢复感知解析；跨 session ref fail-closed 只在 resume 路径 | session_plan 投影无恢复行；analysis recompute 工具无失败子图提示 | M5 retry directive（纯函数+投影披露） |
| F. 解释面缺 what_failed/what_is_safe_to_try/what_needs_you/what_already_done | grep 无此词汇 | M6 explain |
| G. reliability corpus 无恢复场景 | reliability_corpus.py 19 案例全为行为不变量 | M7 recovery_corpus 25+ |
| H. 无接线前后对照基线 | — | M8 perf/security 回归 |
| I. 无恢复成功/安全终止 E2E 门 | G05 无恢复场景族 | M9 |

## 3. 允许改动范围（所有权）

- **新增**：`app/services/gis_harness/recovery_policy/`（contracts/diagnose/policy/explain/risk/continuation）、`app/lib/harness/goal_verification/`、`app/evaluation/recovery_corpus.py`(+runner)、`tests/unit/gis_harness/recovery_policy/`、`tests/unit/harness/goal_verification/`（或对应目录）、`tests/unit/evaluation/test_recovery_corpus.py`、docs/adr/0279。
- **最小挂接**：tool_dispatch_service.py（错误分支 additive 键 + 存量键名 bug 修复）、pi_post_dispatch.py（Disclosure 透传）、agent_pi_bridge.py（详情 payload 组装点）、execution_engine.py/step_error（additive 字段）、session_plan.py（[GIS Recovery] 投影行）、completion/pipeline.py（goal_verification additive 块 + REPAIR 阶段发射）、runtime_repair.py（REPAIR 阶段发射）。
- **不动**：02 协议 adapters、03 时间轴、04 比例尺表达核心；failure_taxonomy 词表（11 类保持，诊断层 additive 引用）；goal_satisfaction 评估器本体（只新增消费投影）。
