# ADR-0119: Semantic Workflow Runtime V5（Executable Typed DAG + Incremental Recompute + Artifact Reuse）

- 状态：Proposed
- 日期：2026-09-09
- 关联：ADR-0118（Semantic Workflow Compiler V4）、ADR-0101（GeoWorkflow
  Recipe & Conformance Foundation）、ADR-0096（GeoCompute 执行平面）、
  ADR-0052（统一 durable job runtime）、ADR-0082（Artifact Runtime）

## 背景

V4 建立了编译期语义层（方法论族/typed DAG/义务继承/WorkflowPackage/
语义 diff/受影响子图），但其生产消费只有两处 evidence-only 投影
（`Plan.workflow_v4` 摘要与 `compile_workflow_semantics` 工具）——
「应该做什么、为什么、有什么义务」从未成为执行事实。Phase A 审计
（`.agent-work/workflow-v5-executable-runtime/00-baseline.md`）证实：

1. 无 workflow 级运行实例（`workflow_instance` 块是派生投影，非 CAS
   执行态机，无 durable resume）；
2. typed ports 仅编译期校验，运行时对实际 artifact 零检查；
3. RecomputePlan 纯函数无 runtime 消费者（重算全图或全手工）；
4. 复用仅 geocompute 节点级 checkpoint，无 workflow 级复用证明；
5. WorkflowPackage emit 后即弃，无 durable registry/semver 发布；
6. **V4 缺陷（已实测复现）**：`compute_affected_subgraph` 在真实
   `to_bounded_dict()` 包形态上闭包失效（边端点带端口后缀，
   `data_role` 变更只标记种子自身）。

## 决策

1. **新包 `app/services/workflow_runtime/`**（Epic 专属 ownership），
   分层消费 V4 产物：`contracts/machine`（typed 状态机）→ `store`
   （DB 持久，实例行 + 每节点行两级 CAS）→ `binding`（运行时 typed
   port 校验）→ `driver`（波次调度）→ `adapters_geocompute`
   （[op, MATERIALIZE] 两节点 ExecutionPlan）→ `reuse`（复用索引）→
   `recompute`（增量闭环）→ `service/projection`（门面/解释）。

2. **不是第二事实源**：包内容事实源 = V4 编译器（注册时 re-emit 比对
   指纹）；会话行状态写手仍 = `_mark_progress`；载荷事实源仍是
   session ref / ArtifactRegistry / DataObject —— 运行时只存节点
   执行态、指纹与指针。

3. **节点状态机（9 态，转移表唯一裁决）**：PENDING/READY/RUNNING/
   SUCCEEDED/FAILED/BLOCKED/SKIPPED/CANCELLED/STALE。关键语义：
   STALE→SUCCEEDED（复用解除零重算）、SUCCEEDED→STALE（失效）、
   RUNNING→READY（恢复专用，租约门控）、CANCELLED 唯一吸收态。
   每节点一行 + `state_revision` 乐观锁（消除整行 JSON 争用）；
   完成类转移永不放弃（幂等完成 OK_IDEMPOTENT）；claim token 仲裁
   双执行者（driver vs chat 通道）；run 租约 + 孤儿清扫实现 durable
   resume。

4. **复用指纹栈（宁可假 miss 绝不假 hit）**：输入内容身份取 live
   descriptor（content sha256 > profile_digest > shape），必带
   `content_revision`（同 ref 原地覆写必 miss）；shape 级只记录不复用；
   env_fp 覆盖 geo 数值栈/编译器/执行契约/方法论/算法注册表版本；
   生效参数值进指纹（参数变化 → 复用失效 → 真重算）；跨 owner 绝不
   共享，anonymous 域内附加 session 同域约束。

5. **增量重算闭环**：semantic change → V4 `compute_affected_subgraph`
   （修复后，完整对象消费）→ quiescence 门（RUNNING 在飞 → pending
   队列，完成边界 drain）→ 节点级 CAS 批量 STALE → 复用裁决 →
   dirty 子图执行。style-only 维科学零触碰（呈现态独立裁决）。
   差分 oracle：runtime STALE 集合 == plan.recompute ∩ {apply 时
   SUCCEEDED|READY|STALE}。

6. **GeoCompute 适配**：可执行节点编译为 [op, MATERIALIZE] 两节点
   计划（session ref 正门出载荷）；显式接线表，未接线诚实
   `NODE_NOT_EXECUTABLE`；`dataset_fingerprints` 携带输入指纹 →
   geocompute 自身语义指纹跨实例去重；执行控制仍归 harness/调用方
   （`GIS_WORKFLOW_RUNTIME_AUTORUN` 默认关）。

7. **chat 集成最小侵入**：三个 fail-open 挂钩（计划合成 attach /
   工具结果锁外 record / MapSpec 突变 style 变更），owner 域由
   Conversation.user_id 推导（信任缝 = 既有会话所有权门），失败只记
   日志零回归。

8. **V4 修复（冻结红线的显式让步）**：`compute_affected_subgraph`
   边端点归一化（防御性剥端口后缀，仅当前缀命中已知节点 id）——
   bug fix 而非语义变更，COMPILER_STAGES 契约零改动，真实包形态
   强闭包回归测试钉住。

## 持久化

migration `0034_workflow_v5_runtime`（链 `0033_geocompute_v6_cluster`；
与 geocompute-v7 撞号时按 rebase 协议重编号）：`workflow_packages`
（(package_id, version) 唯一）、`workflow_instances`（实例级 CAS/租约/
pending/决策环）、`workflow_instance_nodes`（每节点一行 CAS）、
`workflow_node_reuse`（(owner, fingerprint) 唯一，每 owner LRU ≤128）。
全部可重入 DDL + create_all 共存守卫。

## 资源包络

节点 ≤64/实例、attempts ≤3、复用索引每 owner LRU ≤128、changes/pending
≤16、dispatch 并发 ≤4、run deadline 默认 60s（≤300s 服务端上界）、
transitions 节点环 ≤8 / 实例决策环 ≤16。

## Consequences

- workflow-v4 evidence 升级为生产执行事实（durable、可解释、可复用证明）；
- 正向：部分重算替代全量重跑（O(V+E) 计划 + 复用跳过）；负向：每工具
  结果 +1 条索引 DB 写（fail-open、可关停）；复用假 miss（诚实降级）。
- 已知限制：analysis 节点无 GeoCompute 接线时诚实 BLOCKED（科学算子
  执行归 Spatial Science 后续接线）；run 为 in-process 同步驱动
  （deadline 有界），集群派发为 follow-up。
