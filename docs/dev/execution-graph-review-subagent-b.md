# Execution Graph v1 — Subagent B (Final Acceptance Review)

- 分支：`harness/execution-graph-incremental-replan-v1`（7 commits，基线 origin/master @ 580b33e9）
- 评审范围：`git diff origin/master...HEAD`（19 files，+2867/−37）
- 评审人：Subagent B（独立终审，read-only + scoped pytest）
- 日期：2026-09-14

---

## 0. Verification runs（实跑结果）

| 套件 | 结果 |
| --- | --- |
| `tests/unit/gis_harness/test_intent_diff.py` + `test_graph_replan_corpus.py` + `tests/unit/workflow_runtime/` + `tests/test_intent_replan_envelope.py` + `tests/test_pi_session_plan_host.py` | **187 passed** (101s) |
| `tests/unit/gis_harness/test_typed_dag_v4.py` + `test_runtime_bridge_v6.py` | **28 passed** (7.6s) |

无新增失败。两套全绿。

---

## 1. Spec 轴 — **PASS-with-notes**

### (a) 携带完成事实在生产 chat 路径是否真的阻止重执行？——**是，双通道均为真实生产缝**

计划侧（主通道）：
- supersede：`_seed_progress`（session_plan.py:676）把携带行置 complete + bound_ref；replace：void 循环跳过携带行（session_plan.py:815-818），`apply_intent_diff_to_chapter` 把 bound_ref 写回**新 chapter 行**（session_plan.py:71-89）。
- 生产消费点实锤：planner 角色绑定只认 `status ∈ (available, done) 且 bound_ref`（planner.py:1713）；`workflow_instance._derive_bound_refs`（workflow_instance.py:438）同规则 → 数据角色升 bound、义务评估不再产生 data blocker → fetch 不重开；`open_capabilities`（session_plan.py:176）排除 complete 行 → `[GIS Plan]` 投影不再列为 open。
- envelope 级测试（test_intent_replan_envelope.py:74-160）直接驱动 `apply_tool_result`（与 agent_pi_bridge.py:711/843 同一 seam）钉住上述行为。

执行侧（V5 通道）：`apply_intent_facts`（service.py:751-841）lost→`apply_changes`（复用 ChangeApplier + compute_affected_subgraph，零新裁决引擎）；carried→`_chat_complete_node` 拓扑序 SUCCEEDED。注意 V5 侧对 chat 路径是**证据面而非派发面**（Pi 直接派发工具）——阻止重执行的硬闸在计划侧投影 + planner 绑定，V5 SUCCEEDED 是复用/投影一致性。这是架构本意（Pi = Agent Host），文档表述与实现一致。

### (b) replace 与 supersede 全覆盖 + V5 关停降级——**是**

- supersede 分支：facts 在 goal_key 判异后、归档前裁决（session_plan.py:781-786）；replace 分支：`replaced` 为真即裁决（session_plan.py:796）。同 goal_key 重提也走 replace 分支被覆盖。
- `GIS_WORKFLOW_RUNTIME=0`：`record_intent_changes_safe` 早退（hooks.py:153），但 `_intent_facts`（session_plan.py:693-720）不依赖 runtime_enabled → **计划侧携带照常工作**，V5 仅少同步。envelope 测试三例均在此降级路径下验证行为。
- kill switch `GIS_INTENT_DIFF_REPLAN=0` → facts=None → carried 恒空 = master 全量语义（test_kill_switch_restores_full_invalidation 钉住）。

### (c) 10 必做场景 → 真实测试映射——**基本成立，语料有 4 类是恒真重言（见 P2-5）**

| 场景 | 测试 | 性质 |
| --- | --- | --- |
| 1 基线/3 换主体/2 scope | test_intent_replan_envelope.py（生产 seam）+ corpus | 实 |
| 7 resume / 9 fallback / 10 duplicate | test_reliability_scenarios.py（真 Driver + 真持久层）+ plan_runtime seed | 实 |
| 副作用纪律 | test_side_effect_discipline.py（真 Driver） | 实 |
| 4 style / 5 export / 6 resubmit / 8 pin | 仅 corpus，且构造为**零差异章节**（test_graph_replan_corpus.py:97-114） | 重言，savings=1.0 恒真 |

场景 4/6/8 的真实生产机制（MapSpec 域、plan_runtime）不经 `webgis_map_intent` diff——corpus 只证明"相同 chapter 全携带"，不证明那些场景的端到端。指标 0.650 被这 4 类（20/50 例）抬高。

---

## 2. Architecture 轴 — **PASS-with-notes**

- **无平行系统**：intent_diff.py 是纯函数裁决器（零 I/O 零状态）；无新 DAG IR / planner / tool loop / store / registry / context。execution graph = plan_graph + typed_dag + V5 的生产贯通，与 ADR-0184 D1 一致。
- **复用而非重复**：V5 侧走唯一引擎 `apply_changes`（ChangeApplier quiescence defer + STALE CAS）；`workflow_graph` 事件走既有 `SessionPlanEvent`→`events_to_sse` 通道（session_plan.py:634-641），CanonicalPlan 禁用门（session_plan.py:57-60, 363-371）未被触碰（envelope 测试断言 `plan_ready` 不在 SSE）。与 #1275 gis_situation 无重叠（ADR 明确留 adapter 口）。
- **单一写手纪律——声明与实现有出入（P2-1）**：`_seed_progress` 与 `apply_intent_diff_to_chapter` 是 plan.progress / chapter 行 status 的**新直接写点**（与 `_mark_progress` 并列）；master 上 tools.py:1006 本就已是 chapter 行的第二写手。词表一致（ProgressStatus / available/done 投影同规）、全部发生在锁内 envelope 变更路径，**实际上无害**，但 intent_diff.py:17-19 与 ADR D3"行状态唯一写手仍是 _mark_progress / 无第二写手"的表述不准确，应改为"同词表、锁内、仅 intent 分支"。
- **coarse 维分类规则出现第三份实现（P2-8）**：algorithm>parameter>data 优先级现存在于 runtime_bridge.py:327-330（W4）、workflow_v4/diff.py、intent_diff.py:161-174（注释自认"W4 同规则"）。小规则，漂移风险低，但宜收敛为共享 helper。

---

## 3. Reliability 轴 — **PASS-with-notes**

- **apply_intent_facts 在会话锁外的竞态**：穷举后均为保守方向收敛——(i) 与并发 replace/supersede：V5 转移全 CAS；后到的 STALE 覆盖先到的 carry → 多 STALE（保守）；先 STALE 后 carry 洗白的窗口见 P2-3。(ii) `_chat_complete_node` 对 RUNNING/未知起点诚实拒绝（`STATE:*` / `UPSTREAM_PENDING`，service.py:669-701）。(iii) 桥接层锁超时重试（agent_pi_bridge.py:717-737）不可能双跑：TimeoutError 在锁获取时抛出、先于一切 envelope 变更，第二次尝试从干净状态重放。
- **同批 lost→carry 顺序**：单次 `apply_intent_facts` 内 lost 先于 carried（service.py:782/812），同结构下 chapter 闭包保证 carried ∩ lost-closure = ∅。
- **`_topo_order_carried` 环安全**：seen 先于递归置位（service.py:835-850），环按字典序断开；递归深度 ≤ carried ≤ 32；排序失败退声明序，后续 `_chat_complete_node` UPSTREAM_PENDING 兜底。
- **异常半变异**：envelope 变更全部在锁内、`lock.lost` 逐段守卫、save 前置；facts 通道在锁释放后且全程 try/except 包裹（session_plan.py:622-642）——intent 工具路径不可被倒灌。
- **P2-2（too_many_changes 整批静默丢弃）**：service.py:786-805 按 target 展开变更（lost 截 16 但每项 × 节点数），可超 `MAX_APPLY_CHANGES=16` → ChangeApplier 返回 `{"applied": False, "reason": "too_many_changes"}`（recompute.py:86-88）→ **整批 V5 失效丢弃**，仅事件里 `applied:false` 披露。场景：6 个 lost cap × 3 节点目标 = 18 changes。计划侧 void 仍生效，V5 节点滞留 SUCCEEDED（陈旧证据面）。建议按节点截断而非整批拒绝。
- **P2-3（STALE 洗白窗口）**：chapter depends_on ⊋ V5 边的结构差下，carried 节点可在同批先被 apply_changes STALE、再被 `_chat_complete_node` 以**旧 ref** 推回 SUCCEEDED（service.py:690-695 STALE→READY 路径）。test_intent_facts_service.py:159-176 钉的是"新 receipt ref"变体，生产 intent-carry 传的是旧 bound_ref，测试未钉住生产语义。跨调用交错同理。兜底：复用指纹（输入指纹失配必 miss）+ W5 artifact health + 下次工具结果 STALE 重驱自愈。方向保守、可达面窄。
- **destructive 纪律**：driver 不自动重试（driver.py:791-798）、STALE 不重入队（driver.py:216-222）；唯一重驱通道 `retry_failed_nodes(force)` 对 FAILED/STALE 均可达（service.py:430-470）。副作用测试（真 Driver）钉住。P2-6：首次失败即发 `RETRY_EXHAUSTED` journal kind（attempt=0），语义错位（应为专门 kind / reason-only）。
- **resume / 孤儿复位 / 跨租户**：test_reliability_scenarios.py 以真 SQLite + 真 Driver 覆盖（孤儿租约过期 1.2s 实等），reuse 跨 owner 必 miss 有测试。

---

## 4. Performance / Security 轴 — **PASS**

- 全部有界：rows ≤64、carried/lost ≤32、事件 ≤8 项、实例 ≤2、变更 ≤16、字符串截断一致；`workflow_graph` payload 无时间戳、无 ref 明文（只有 capability 名）。
- `diff_chapters` 最坏 O(passes×caps×sig) ≈ 65×128 次小 dict 哈希；`_topo_order_carried` 单次整 plan 读——均可忽略。
- **P2-7**：`canonical_params`（intent_diff.py:177-188）对嵌套 params 无深度限制；深嵌套 payload（planner 产出，非直接客户端输入）→ RecursionError → `_intent_facts` 捕获 → 全量失效降级。fail-safe（只损失携带优化，等价 master），可加深度上限。
- 租户缝不变：`record_intent_changes_safe` 经既有 `owner_scope_for_session`（hooks.py:23-53，与 record_tool_result_safe 同信任缝）；`list_session_instances`/`get_instance`/ChangeApplier 全程 owner 谓词；`_chat_complete_node` 的 instance_id 只来自已 scoping 的清单。会话 id 入日志为 repo 既有规范。
- SSE 注入面：replanned payload 仅 capability/dimension 短串（graph_events.py:56-78），来自 planner 行而非原始用户文本；经既有 `sse_event` 编码。

---

## 5. 发现汇总

### P0（阻断）
无。

### P1
无。

### P2
1. **单一写手声明失准**：intent_diff.py:17-19、ADR-0184 D3 称"唯一写手 _mark_progress / 无第二写手"；实际新增 `apply_intent_diff_to_chapter`（session_plan.py:71）与 `_seed_progress`（session_plan.py:676）两个 status 写点（同词表、锁内、无害，且 master 上 tools.py:1006 已是第二写手）→ 改文档措辞。
2. **V5 失效整批丢弃**：service.py:789-800 变更按节点展开可超 MAX_APPLY_CHANGES=16 → recompute.py:86 整批 applied=False，lost 同步静默降级（事件披露 applied:false）→ 建议按节点截断。
3. **STALE 洗白窗口**：service.py:690-695 chat STALE 重入队以旧 ref 结算；chapter 闭包与 V5 闭包结构差/跨调用交错下可洗白，test_intent_facts_service.py:159 用新 ref 未钉住生产旧 ref 语义 → 补一条旧 ref + V5 先 STALE 的测试或加 ref 一致性守卫。
4. **`node_states_event` 无生产调用方**（graph_events.py:81-111，grep 全仓仅测试引用）：`node_states` 词表未接任何路径；ledger M3 / ADR D7 把两种 payload 并列为已交付契约 → 标注 reserved 或接线。
5. **语料重言**：test_graph_replan_corpus.py:97-114 场景 4/6/7/8 新旧 chapter 全同（零 diff），savings=1.0 恒真；20/50 例无实际变化，ledger 的 0.650 均值被抬高 → 指标注明口径或让 4/6/8 走各自真实域的断言。
6. **journal kind 错位**：driver.py:791-797 首次失败（attempt 可为 0）即记 `RETRY_EXHAUSTED` kind，reason=DESTRUCTIVE_NO_AUTO_RETRY → 换专用 kind 或仅用 reason。
7. **canonical_params 无递归深度上限**（intent_diff.py:177）：深嵌套 payload 触发 RecursionError → 全量失效降级（fail-safe），加深度上限更干净。
8. **coarse 维规则三处实现**（intent_diff.py:161 / runtime_bridge.py:329 / workflow_v4/diff.py）→ 收敛共享 helper（可后置）。

---

## 6. 结论

**PR-go。** 无 P0/P1。四个轴全部 PASS（其中 Spec / Architecture / Reliability 为 PASS-with-notes）。验收链最关键的一环——「携带完成事实在生产 chat 路径真实阻止重执行」——经生产消费点（planner.py:1713 角色绑定、plan_graph 投影、V5 attach 预绑）核实为真接生产，非纸面实现。上述 8 项 P2 均可在本 PR 内小改（1/4/6 文档与一词换用）或作为紧随的 follow-up（2/3/5/7/8）；不构成合入阻断。
