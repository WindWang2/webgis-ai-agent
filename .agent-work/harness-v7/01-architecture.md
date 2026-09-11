# Harness V7 Architecture — Long-Horizon Contextual GIS Agent Runtime（草稿，待审计修正）

目标链路：
`Intent → Context Assembly → Planning → Capability Retrieval → Execution →
Observation → Critique → Repair/Replan → Partial Recompute → Finalization → Context Commit`

全部决策 additive：不建第二 planner、不复制业务逻辑、不重建 Pi、不改
WorkflowInstance/Finalizer/检索既有契约 —— V7 把**已存在的状态机散件**
编排成一个显式、可追踪、可恢复的 runtime 状态机。

## 设计原则（承 V4-V6 红线）

1. **单一事实源不动**：SessionPlan.gis_chapter 仍是计划事实；
   WorkflowInstanceState 仍是阶段态投影；map_product 仍是终验结论。
   V7 新状态全部 additive 单键，可随时由权威状态重建。
2. **确定性优先**：同输入同输出；LLM 只建议，收敛由确定性裁决
   （goal_graph.validate_candidate_graph 同纪律）。
3. **有界一切**：节点/转移/循环历史/预算全部有上界；预算耗尽 →
   诚实 abort_with_disclosure，绝不无限对抗。
4. **生产驱动点真实存在**：不为「可能的回路」记账（V6 R1 M4 教训）——
   每个新回路的驱动点与预算同 commit。

## D1 Harness Runtime State Machine（Phase A）

新 `app/services/gis_harness/runtime_state_machine.py`：

- 封闭状态词表（runtime 级，正交于 StageState）：

    IDLE → INTENT_RESOLVED → CONTEXT_ASSEMBLED → PLAN_READY → EXECUTING
      → OBSERVING → CRITIQUING → (REPAIRING | REPLANNING | RECOMPUTING)
      → FINALIZING → COMMITTED / ABORTED / SUSPENDED

- 合法转移表（machine-readable）；非法转移 fail-closed + 披露。
- 每次转移 = RuntimeTransition（revision 单调、from/to、trigger、
  reason_code、有界环形历史）持久化于 `gis_chapter["runtime_state"]`
  additive 单键；与 WorkflowInstanceState 同纪律（无时间戳，指纹化）。
- 触发器（既有生产事件）作为转移驱动输入：tool_result / turn_settled /
  render_observation / resume / supersede。
- 恢复：session 重启后从持久化 runtime_state + 权威章节事实重建
  （派生优先，不机械 replay —— 与 continuation 同语义）。

## D2 Long-Horizon Plan/Replan（Phase B）

- PlanVersion：plan_id + version + fingerprint（rows_fingerprint V2 同源）；
  plan 变更（supersede/replace/replan）→ 版本 +1，历史有界保留。
- 失败分类 → 动作裁决：复用 failure_taxonomy + continuation.decide_continuation，
  **新增 replan 驱动点**（V6 follow-up）：finalize/execution 失败且
  repair 预算耗尽 → replan（有界 LOOP_BUDGETS.replan）。
- Partial recompute：复用 workflow_v4/recompute.compute_affected_subgraph
  （dependency-aware affected set），从「从头重跑」改为「重算受影响节点」。
- 幂等/重复执行保护：action_fingerprint（runtime_repair 同款）进 plan step
  记录；同指纹重复执行拒绝并披露。
- Rollback point：plan 版本 + artifact 指纹快照（有界 ≤N），replan 失败
  可回退到上一稳定版本语义（披露而非静默）。

## D3 Durable Context 分层（Phase C）

扩展 `durable_context.py`（不推翻三层词表，在其上建九域投影）：

- ContextDomain 词表：turn/session/project/workspace/map/data/workflow/
  artifact/capability —— 每域一个有界投影块（rebuildable 优先，
  durable facts 白名单进锚点）。
- ContextCheckpoint：turn 边界把九域快照（有界）写入锚点 additive 键；
  crash recovery = anchor 重注入 + 域投影重建。
- ContextBudget：chat/context_budget.py 已管 prompt 组装预算（不动）；
  V7 补的是 **durable 侧预算**（每域字节上界 + 总预算 + 超限裁剪优先级
  + 违规留痕），复用其裁剪优先级思想。
- Compact/summarize：域投影超界 → 摘要压缩（确定性摘要，无 LLM 依赖）；
  history 有界环形。
- Freshness/provenance：每域带 source_fingerprint + updated_revision；
  过期域由权威状态重建，不信任旧值。
- Conflict resolution：同域多写者 → revision 单调 + 后写胜 + 冲突披露
  （与 durable_context 读改写纪律一致）。

## D4 Capability Retrieval V7（Phase D）

- CapabilityDescriptor：结构化描述符（id/kind/kind∈{tool,algorithm,template,
  component,model,workflow}、preconditions/postconditions、input/output
  schema ref、CRS/geometry/raster 兼容面、cost estimate、reliability、
  latency profile、fallback chain、success feedback 计数）。
- 来源：既有 capability registry / algorithm registry / template catalog /
  component resolver / methodology registry —— **只读聚合投影，不复制
  业务元数据**；描述符由各 registry 派生（单一事实源）。
- RetrievalRequest/Scoring：query intent + 输入特征（geometry type、CRS、
  raster/矢量、规模）→ preconditions 过滤 → 信号融合打分（V6 hybrid
  4+1 路之上加 descriptor 结构信号）→ fallback chain 输出。
- Historical success feedback：recovery_ledger 成功/失败计数 → reliability
  信号（durable，已有持久化面）。
- Gold corpus 结构化：新增**生成式语料**（scenario grammar：域 × 对象 ×
  动词 × 修饰 → 确定性展开 2k-5k 场景 + 采样评估集），硬编码金标保留
  为锚点子集；评测门按可演进结构（注册表驱动）而非行数。

## D5 Map-aware Observation & Critique（Phase E）

- 扩展 render_observation/observation_states 消费面 →
  `map_critique.py`：从 observation payload + MapSpec + 派生几何做
  确定性检查词表：blank_map / layer_extent_mismatch / invalid_bounds /
  component_overlap（已有 derive_component_layout_findings）/ legend_presence /
  title_presence / scalebar_presence / north_arrow_presence / colorbar_presence /
  label_collision（有界启发式）/ export_completeness（已有 assess_export_parity）。
- Critique → local repair：可修复项走既有 mutation 通道（runtime_repair
  的 reassert/patch）；不可修复 → findings 披露。
- 闭环：observation → critique → repair → re-observe（复用
  continuation 的 repair 回路 + 预算）。

## D6 Finalization Hook（Phase F）

- IntentAcceptanceCheck：从章节意图事实（query/required layers/components/
  方法族）对 map_product + observation_health 做确定性验收判定 ——
  替换 `intent_verified=(result.status=="complete")` 的循环论证为独立
  证据判定（intent 声明 ↔ rendered 证据逐项核对）。
- finalizer 直连 decide_continuation（V6 follow-up）：needs_repair 裁决
  经 continuation 决定 repair / replan / reobserve / abort 披露。
- Context commit：READY 裁决后把九域 context 快照 + artifact lineage
  提交进锚点（durable），供下一 turn 增量继续（「只看武侯区并改蓝色系」）。
- FinalDisplayConfirmationHook：默认 auto-confirm（无人工）；架构留
  confirmationToken seam（显式接口 + 默认实现），不新增前端依赖。

## D7 Delegation（Phase G）

- Handoff schema：SubagentTaskSpec 之上加 plan-step 绑定（handoff_id /
  plan_step / role / required_outputs / budget class / deadline）。
- Parent 跟踪：delegation 注册表（有界）挂 runtime_state；子任务完成
  → 证据/refs 回填 plan step；失败 → continuation 裁决（重试换 role /
  升级主会话 / 放弃披露）。
- 递归红线沿用：depth≤2、并行≤2、budget roll-up 既有语义不变。

## 与并行 Epic 边界

- 不碰：workflow compiler 本体、GeoCompute scheduler、渲染器、extensions、
  chat context 组装既有契约（budget/assembler 只读复用）。
- 共享面：CHANGELOG 追加；ADR 独立编号（0121 起，空位核实后定）；
  零 migration；无 CI 修改。

## 测试 oracle

- 状态机：转移表穷举 + 非法转移拒绝 + 持久化重建等价。
- Plan/replan：失败注入 → replan 驱动 → affected subgraph 只含受影响
  节点；幂等拒绝；回滚点。
- Context：checkpoint/reload 往返等价、预算裁剪确定性、冲突、freshness。
- Retrieval：descriptor 覆盖生成语料抽检、fallback chain、反馈更新。
- Critique：每检查项正/负用例（合成 observation payload）。
- Finalization：意图不满足 → 拒绝 READY；验收 → context commit。
- Delegation：handoff 往返、失败回收、depth 红线。
