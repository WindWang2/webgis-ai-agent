# F04 — Typed Context Assembly & Budget Governor：Recon（执行时基线）

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，PR #1494 合入）。
- 执行时间：2026-09-26。执行前 `git fetch origin --prune` 复核，无更新提交。
- 分支：`zcode/f04-typed-context-assembly-budget-20260926-9e1ad229`；worktree：`../wt-webgis-f04-typed-context-assembly-budget-20260926-9e1ad229`。

## 1. Already Done（已存在，禁止重复实现）

| 能力 | 位置（9e1ad229） | 状态 |
| --- | --- | --- |
| `HarnessTurnContext`（hk.ctx.v1） | `app/services/harness_kernel/models.py:425`；builder `harness_kernel/context.py:build_turn_context`；`GISSessionRuntime.turn_context`（runtime.py:1127） | #1485 已落；唯一消费方是 end_turn settle 日志 |
| mission working context / context card | `app/services/gis_context/*`（scope.py 的 `ScopeTier`/`SensitivityClass`/`ScopeRef.renderable_in`；working_context.py 的 revision CAS + mark_stale；hotpath.py `assemble_gis_context_card` 带 `ContextCardReceipt` miss-reason） | #1487 已落，已接入 chat `_build_cartography_turn_context` |
| 分层 scope 词汇 | `gis_context/scope.py`（sensitivity/policy/边界门） | 复用，不复制 |
| session 分域上下文层 | `app/services/gis_harness/context_layers.py`（9 domain、`DOMAIN_BYTE_CAPS`、`TOTAL_BYTE_BUDGET=3072`、revision+fingerprint checkpoint） | V7 已落（completion/checkpoint 面，非 Pi prompt 面） |
| turn 情境投影 | `app/services/gis_situation/turn_context.py`（compile→diff→advance 只前进；ADR-0190 主动记忆切片） | 已接 chat.py 两个 Pi 调用点；**有副作用（advance 快照），不能被 provider 重复派生** |
| 预算规划/度量（legacy 引擎面） | `app/services/chat/context_budget.py`（`Category` 裁剪序、`GIS_SECTION_CAPS`、`plan_budget`、`BudgetReport`、`GisBudgetAdvisor` measure-don't-trim） | 复用其 Category/规划器；F04 在其上做 Pi 面分配执行 |
| token 估算单一语义 | `app/services/chat/context/history_compression.py:_estimate_tokens`（CJK-aware） | 复用，不新造 estimator |
| governor 资源契约 | `app/services/governor/contract.py`：`Dimension.CONTEXT_TOKENS`、`Subsystem.LLM_CONTEXT`、`ResourceClass.LLM`、`ResourceDemand/Budget/Reservation/Usage/Decision`；`governor.admit_and_reserve/complete`（estimate-vs-actual 观测） | 契约齐全，**只缺 LLM context 的真实 plan/settle 调用方** |
| actual 记账钩子 | `governor/context_link.py:record_context_report` → `session_budget.SessionBudgetLedger.record_context_tokens`（cumulative per turn/session） | legacy assembler :780-789 已接；Pi 面 无 |
| 数据栅栏/转义 | `app/services/chat/context/formatters.py`（8 个 `TAG_UNTRUSTED_*` + `_xml_fence` + `_untrusted`）；`prompt.py:39-46` 系统提示栅栏契约；`pi_turn_context._neutralize_active_tools_markers`（控制面 marker 中和） | 复用；F04 补统一应用点 + secret scrub |
| secret 消毒 prior art | `app/services/jobs/redaction.py`（key denylist）、`app/lib/harness/replay/sanitize.py`（key+value 双层） | F04 以同等纪律在新模块内实现（见 Must Not Touch：`app/lib/redaction.py` 归 F09） |
| receipt/trace 原语 | `app/lib/runtime/evidence.py`（settle once-first-wins）、`app/lib/runtime/gis_trace.py:record_stage`、OM `app/lib/runtime/decision_record.py`（canonical json/reason code） | receipt 接 record_stage + 有界日志 |
| Pi turn prompt 拼接现状 | `app/services/chat/pi_turn_context.py`：`bind_turn_prompt`（plan/spec→plan_block+surface+active_tools+v6+tombstone）+ `attach_turn_context`（8 块字符串拼接，marker 最后） | **F04 的主迁移对象** |
| Pi 三字符串签名 | `agent_pi_bridge.py:1845`（prompt）/:2285（stream）→ `bind_turn_prompt(message, token, sid, cartography_context, env_block)`；上游 chat.py:1029/:1327 `_build_cartography_turn_context`（verdict+memory+knowledge+gis_memory+gis_context 五块拼接）与 :1043/:1343 `_build_situation_env_block` | **ADR-0208 D2 明确的 deferred seam** |

## 2. Still Missing（F04 要补的）

1. **typed `ContextProvider`/`ContextItem` 契约**：scope/revision/freshness/sensitivity/est/priority/evidence-ref —— 无。
2. **Pi prompt 入口 typed 化**：cartography 五块仍以自由字符串跨层（route→bridge→bind）传递；plan/surface/v6/tombstone 在 bind 内无预算、无去重、无 receipt。
3. **Pi 面预算分配器**：legacy `context_budget` 只 measure-advise；Pi 面没有任何 cap/floor/让位。
4. **governor LLM context 维度真实接线**：`Subsystem.LLM_CONTEXT` 无任何 demand/reserve/complete 调用方；Pi 面不记 context token actual。
5. **跨域确定性去重**：五块 + v6 + verdict 之间无 fingerprint 去重（如同一 verdict 文本经 `[CARTOGRAPHY_VERDICT]` 与 V6 map-situation 双通道出现）。
6. **secret scrub**：knowledge/memory/tool 文本进 prompt 前无 key/value 双层消毒。
7. **`ContextAssemblyReceipt`**：Pi 面无任何「进了什么/省略了什么/为何」的机器可读记录。
8. **性能面**：chat.py 取 (map_state, mapspec) 后 bind_turn_prompt 再取 (plan, mapspec, map_state) —— 每 turn mapspec×2、map_state×2 的重复读。

## 3. Must Not Touch（open PR 热区，绕行）

| 文件 | 占用 PR | F04 策略 |
| --- | --- | --- |
| `app/api/routes/chat.py` | #1498(F13)/#1500(F15) | 仅 2 个最小 hunks（把预拼 cartography_context 换成结构化参数）；不重排周边 |
| `app/services/governor/contract.py`/`estimation.py`/`dispatch_adapter.py`/`plan_aggregation.py` | #1499(F08)/#1503(F09)/#1498(F13) | 零修改，纯 import 消费既有契约 |
| `app/lib/redaction.py` | #1503(F09) **新建** | F04 不创建同名文件；secret scrub 放 `app/services/context_assembly/fence.py` |
| `app/lib/runtime/{evidence,gis_trace,decision_record}.py`、`app/services/chat/execution_engine.py`、`app/lib/harness/replay/*` | #1503(F09) | 只 import 消费，不修改 |
| `app/lib/cartography/quality_loop.py` | #1502(F10) | 只读 `cartographic_fingerprint` |
| `app/services/gis_harness/completion/*` | #1500(F15) | 不触碰 |
| `app/services/mapspec_store.py`/`lifecycle_engine.py` | #1501(F11)/#1502(F10) | 只读 |

## 4. Integration Seams（F04 落点）

- 新包 `app/services/context_assembly/`（contract/fence/dedupe/allocator/receipt/governor_link/providers/assembly/flags/legacy_compat）——全新文件，无冲突。
- `app/services/chat/pi_turn_context.py`：`bind_turn_prompt` 变兼容 adapter（typed 默认、kill-switch 回落 legacy 字节等价路径）；`attach_turn_context` 保留为最终顺序渲染器。
- `app/agent_pi_bridge.py`：`prompt`/`stream_prompt` 增加可选结构化上下文参数（additive kwargs）并透传 turn_id。
- `app/api/routes/chat.py`：两处调用点删预拼、传结构化参数（最小 hunks）。
- 测试：`tests/unit/context_assembly/*` + 邻域回归（test_pi_turn_context、test_cartography_turn_injection、governor、harness_kernel_lifecycle）。

## 5. Overlap 表（open PRs ↔ F04）

#1489 无关；#1497(F12) 只共享 decision_record 的读面；#1498(F13)/#1500(F15) 共享 chat.py（tight hunks 策略）；#1499(F08) 共享 governor 契约（只读消费）；#1501/#1502 无写冲突；#1503(F09) 共享 runtime receipt 原语（只读消费，redaction 不同名）；#1504(F14) 无关。**结论：F04 以全新模块为主、热点文件最小 hunks 接线，可安全并行。**

## 6. 关键设计决策（实现前锁定）

1. **单一真相**：provider 只包装既有单渲染源 builder（format_session_plan_projection / v6_context_blocks / verdict_summary / context_assembler._build_project_memory_block / project_knowledge / gis_memory / gis_context.hotpath / situation）；不复制渲染逻辑、不建第二 knowledge/context store。
2. **situation 块保持 caller-injected**：`build_situation_turn_context` 有快照 advance 副作用，provider 不得重复派生；env_block 以带 provenance 的 injected item 进入管线。
3. **cartography 五块迁移进管线**：从 chat.py 预拼改为 bridge 内 provider 直接从 authority 派生（顺带消除 mapspec×2/map_state×2 重复读）；`_build_cartography_turn_context` 保留为薄包装（测试/退役边界用）。
4. **预算执行诚实省略**：cap 超限时优先整项省略（reason-coded），floor 保护项（verdict/plan/user/marker）有界截断并留痕；kill-switch `GIS_TYPED_CONTEXT_ASSEMBLY=0` 回落字节等价 legacy 路径。
5. **governor observe-first**：默认 observe（provisional 纪律），`CONTEXT_ASSEMBLY_ENFORCE=1` 才允许 reject 语义；estimate/actual 用同一 `_estimate_tokens` 语义，settle 进 `record_context_tokens` + receipt reconcile。
6. **user message 不可变**：marker 中和沿用既有实现（legacy 同款），不做额外改写。
