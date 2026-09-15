# ADR-0187: 专业化空间智能体集群总控编排器（specialist-subagent-swarm-orchestrator v1）

- 状态：Proposed（随 `agent/03-specialist-subagent-swarm-orchestrator` 分支评审）
- 日期：2026-09-14
- 关联：ADR-0184（Execution Graph + Incremental Replanning）、
  ADR-0180（Pi-native Harness Kernel / SessionPlan）、ADR-0104（Subagent
  并行委派）、ADR-0101（Subagent 角色档与层级预算）、ADR-0076（SessionPlan
  事实源）
- 编号说明：任务书（agent-03 track）指定占用 0187；0185/0186 为姊妹 track
  （01/02）预留区间，本 ADR 不与其争用。

## 背景

当前全部用户请求由单一 Agent 宿主（`app/agent_pi_bridge.py` 驱动的 Pi 进程，
或 legacy ChatEngine）在一个串行长会话内完成。对跨越「多源异构数据获取 →
大规模拓扑/栅格计算 → 专业制图排版 → 多轮质量审计」的复合空间任务，单
Agent 面临：

- **上下文长度压力**：全量对话历史 + 工具产出在单会话内线性累积；
- **长程规划漂移**：串行多阶段任务的中间结果污染后续决策；
- **执行阻塞**：任一长耗时步骤锁死整个对话通道，无法并行加速；
- **无失败隔离**：单点异常沿串行链路传播，缺乏局部重试/降级路径。

Phase 0 勘察（本 ADR 撰写前对 master @ 3eb2cc6a 的实读）确认已有三层可复
用骨架：

1. **执行图账本层**（ADR-0184/V5）：`app/services/workflow_runtime/` ——
   `machine.py` 纯函数（`build_adjacency` / `ready_set` / `downstream_closure` /
   `check_transition`）、`contracts.NodeState` 状态词表与合法转移表、
   `Driver`（拓扑波次、有界并发、CAS claim）。ADR-0184 后果段明示其
   autorun 策略未开启，「这正是 Swarm 总控可能的接合点」。
2. **会话事实层**（ADR-0180/ADR-0076）：`SessionPlan` 信封 +
   `CapabilityProgress(capability/status/bound_ref)` 行状态词表 +
   `session_lock_registry` fail-closed 会话锁 + `load/save_session_plan`；
   ref 提货券总线（`session_data.store/get`、`artifact_registry`、
   `ref:` 前缀纪律、「plan payload 永不内联 GeoJSON——artifact 永远是 ref」）。
3. **子代理委派先例**（ADR-0104/0101）：`app/services/subagent.py`
   `SubagentDispatcher.run(task=, role=, ...)`（递归深度 ≤2、失败隔离、
   诚实 settle）、`subagent_roles.SubagentRole`（角色是策略不是执行体）。
   其模块级不变式：**Pi/主引擎是宿主，子代理不是第二 Agent 框架**。

真正缺口（S1-S4）：

- **S1**：没有跨专业角色的 DAG 级任务分派——`run_parallel` 是无依赖的
  扁平扇出（信号量 2），表达不了「数据→计算→制图→审计」的拓扑；
- **S2**：子代理产出只有扁平 `SubagentResult.refs`，没有面向集群的
  提货券聚合与回写主会话计划的凭证总线；
- **S3**：并发上限是模块常量（2/6），没有「集群级全局熔断」语义，多个
  编排入口叠加时可超卖执行槽；
- **S4**：Pi bridge 层没有受保护的集群委派网关（特性开关、主 turn 互斥、
  有界输入）。

## 决策

### D1 — Master-Specialist 拓扑：Swarm 是「主 Agent 之上的委派织物」，不是第二 Agent 框架

- **Master** = 既有 Pi 宿主（`agent_pi_bridge.PiBridge`），职责：意图解构
  触发、集群委派请求发起、最终答复合成。Pi 的 tool/LLM/history loop
  **原样保留**，本 ADR 不复制、不旁路（沿用 ADR-0180 D-002 纪律）。
- **Specialist** = 四类专业子代理运行时：`data_hunter`（数据猎手）、
  `compute_specialist`（计算专家）、`cartography_specialist`（制图专家）、
  `audit_judge`（审计裁判）。角色是**策略标签**（映射到
  `subagent_roles` 角色档），不是新的执行体框架；v1 生产适配器直接包装
  `SubagentDispatcher.run`。
- 编排器只拥有「**分解 → 调度 → 派发 → 聚合**」四步，无 LLM、无工具面；
  v1 分解器是确定性启发式（关键词→相位管道），LLM 分解器留作可插拔口。

### D2 — 不新造第六套 DAG：复用 Execution Graph 的状态词表与调度数学

Swarm 子任务图**不引入新图 IR**：任务状态直接取
`workflow_runtime.contracts.NodeState` 词表（PENDING/READY/RUNNING/
SUCCEEDED/FAILED/SKIPPED/CANCELLED；STALE/BLOCKED 本层不用）；就绪集
计算、邻接构建、下游闭包、转移合法性全部经 `ExecutionGraphPort` 适配器
委托给 `workflow_runtime.machine` 同名纯函数。Swarm 层新增的仅有：

- `DEGRADED` **不是**新节点状态——降级路线表达为 `receipt.status=degraded`
  + 节点状态落 `SKIPPED`（对调度器而言与跳过同构：下游视为已结算），
  降级事实记录在 receipt 与 manifest 中披露；
- `SwarmTaskDescriptor.side_effect` 沿用 D6（ADR-0184）副作用词表
  `pure|derived_external|destructive`，**destructive 任务 at-most-once：
  失败不自动重试**，与 V5 纪律逐字对齐。

与 V5 DB 账本的关系：v1 集群运行态是**进程内有界对象**（不建
swarm_instance 表），与 V5 的进一步贯通（swarm 子任务作为 V5 实例节点、
`record_tool_result` 回写）留作 K 级后续，接口缝已由 `ExecutionGraphPort`
与 receipt 通道预留。

### D3 — 上下文隔离（Zero Big Data in Context）：切片进、提货券出

- **输入切片**：子代理严禁接收全量对话历史。唯一合法输入 =
  `SpecialistAssignment`，由三部分组成：①任务切片 `SwarmTaskDescriptor`
  （自身 goal，非父目标全文）；②世界态投影 `WorldStateProjection`
  （有界事实行 ≤24 条、每条 ≤240 字符、相关 ref ≤24 个、约束 ≤12 条，
  **零 payload**，`relevant_refs` 强制 `ref:` 前缀，违反即 ValidationError
  fail-closed）；③直接上游 receipts 摘要（同样只有 ref 与 ≤400 字符
  summary）。
- **输出提货券**：子代理唯一合法回传 = `SubagentReceipt`：
  `produced_refs`（≤12 个、强制 `ref:` 前缀）、有界 summary/error、
  attempt/心跳/墙钟记账。**任何 GeoJSON/栅格/大文本一律不入上下文**，
  payload 经既有 `session_data` ref 总线落库。
- 两端约束都在 Pydantic validator 层强制（截断 + 拒绝），dispatcher 归一化
  层二次强制（`normalize_receipt`），测试逐条断言。

### D4 — 集群级并发熔断：全局 Governor 单例，硬上限 ≤3

- 进程级单例 `SwarmConcurrencyGovernor`（`get_swarm_concurrency_governor()`）：
  每 event-loop 一把 `asyncio.Semaphore(3)` + 活跃 assignment 台账；
  **任何** SwarmOrchestrator 实例派发任何子任务前必须经其 `acquire()`，
  释放必须 `finally release()`。多编排器/多会话并发叠加时全局在飞子任务
  依然 ≤3（测试以双编排器交叉验证）。
- 调度语义 = 队列背压 + 流水线唤醒：就绪任务全部生成 launcher 协程，
  launcher 在信号量上排队（任务态诚实停在 READY=「已可派发未 RUNNING」，
  与 `machine.ready_set` 的 READY 语义一致）；任一完成释放槽位即唤醒队首
  （信号量原生语义）。按 `priority` 降序 + task_id 字典序稳定排序，同输入
  同序（沿用 V5 确定性纪律）。
- 任务级 `timeout_s` 硬墙钟（`asyncio.wait_for`）+ 心跳记账
  （`on_heartbeat` 回调，v1 仅观测不入超时判定，扩展缝已留）。

### D5 — 失败隔离与局部重试：单点失败绝不击穿集群

- 每任务独立重试策略（`max_retries ≤ 2`，attempts ≤3，与 V5
  `MAX_NODE_ATTEMPTS` 对齐）；异常分类 `error_code ∈
  {retryable, non_retryable, timeout, cancelled}`，仅 retryable/timeout
  自动重试；destructive 一票否决自动重试（D2/D6）。
- 单任务终态 FAILED → 仅其**传递下游闭包**
  （`machine.downstream_closure`）转 SKIPPED，兄弟分支与无关节照常流水；
  `optional=True` 的任务重试穷尽后走降级路线（receipt=degraded、节点
  SKIPPED、下游按已结算继续）。
- launcher 体内全量异常兜底：任何异常都折算成 FAILED receipt 落账，
  `run_swarm` 本体不因子任务异常退出（测试断言「失败后集群仍 settle」）。
- 集群终态裁决：全 SUCCEEDED/SKIPPED（含降级）且无 FAILED → `succeeded`；
  仅 optional 分支受损 → `partial`；存在非 optional FAILED → `failed`；
  主动取消 → `cancelled`。

### D6 — 凭证总线聚合：manifest 提货单 + SessionPlan 有界回写

- 依赖闭包全部到终态后，`SwarmAggregator` 汇集 receipts →
  `SwarmAssetManifest`（≤32 条目：ref_id/capability/role/task_id/summary）；
  注入 `SessionStoreProtocol` 时逐 ref `ref_exists` 验券，缺失降级披露。
- 回写主会话：默认 `SessionPlanSwarmSink` 在 `session_lock_registry`
  fail-closed 会话锁内 load→upsert `CapabilityProgress(status="complete",
  bound_ref=首产 ref)`→save（revision CAS 自增）；manifest 全量落
  session store（`prefix="swarm"`）得 `manifest_ref`，计划内只留提货券。
- 回写全程 **fail-open**：任何异常只记日志返回 None，绝不影响集群 settle
  结果（与 workflow_runtime hooks 的附加事实通道同纪律）。

### D7 — SwarmBridge 受保护网关：默认关、互斥、有界

`agent_pi_bridge.SwarmBridge`（薄类，惰性 import agent_swarm）：

- **特性开关** `GIS_SWARM_ORCHESTRATOR`（默认关，off 值词表与
  `GIS_SUBAGENT_PARALLEL` 一致）——关闭时诚实拒绝，零行为漂移；
- **主 turn 互斥**：`_active_turns` 表中该 session 存在在飞 Pi turn 时
  拒绝（`master_turn_active`），防止与主对话通道并发写会话态；
- **有界输入**：goal ≤2000 字符，超限拒绝；
- **有界输出**：仅返回 run_id/终态/状态计数/manifest refs 摘要，无 payload。

### D8 — 明确不做（v1 非目标）

- 不做 LLM 任务分解（v1 确定性启发式；`SwarmTaskDecomposer` Protocol
  为后续 LLM 分解器留口）；
- 不做跨 worker/跨 pod 分布式集群（单进程语义；跨进程一致性仅借既有
  Redis 会话锁）；
- 不做 swarm 运行态 DB 持久化与断点续跑（receipts/manifest 已入
  session store，恢复语义留待与 V5 实例贯通时一并设计）；
- 不改 Pi 事件循环/SSE 通道（`session_plan_*` 事件族冻结纪律不变，
  swarm 结果经普通工具回执路径披露）。

## 后果

**正收益**：复合空间任务获得 DAG 级并行（≤3）与专业角色分工；子代理
上下文从 O(全量历史) 降到 O(切片+券)；单点失败有局部重试/降级/跳过
三档语义；集群并发有全局熔断硬界；全部调度数学与状态词表复用 V5 纯函数，
零新图 IR。

**代价与风险**：

- 集群运行态在进程内存中，pod 重启丢失在飞 swarm（接受：子产物 ref 已
  落 session store，可由后续恢复机制扫券重建）；
- 全局 Governor 是进程级单例，测试需按 loop 重建信号量（实现已处理）；
- 启发式分解对未命中关键词的复合任务退化为通用四相位管道，精度让位
  确定性（接受：LLM 分解器可插拔）；
- Swarm 与 V5 DB 账本暂未贯通，双重记账（swarm 内存态 vs V5 实例行）
  在贯通前由 D8 边界约束（swarm 不写 V5 行，V5 不读 swarm 态）。

**验证**：`tests/unit/test_swarm_orchestrator.py`（TDD 先行，≥20 用例：
DAG 映射/背压熔断/失败隔离/重试/降级/destructive 纪律/提货券防线/
聚合回写/网关守卫），全部离线确定性（假 runtime、无 LLM、无网络）；
协议规范见 `docs/dev/swarm-orchestrator-protocol.md`。
