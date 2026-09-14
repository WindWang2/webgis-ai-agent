# PR body 草稿（swarm-v1 / ADR-0187 · agent-03）

> 最终 body 以本文件为准。

## Summary

agent-03 任务书（专业化空间智能体集群总控编排与任务分派）落地。Phase 0 勘察
对账：任务书点名的 `ExecutionGraphDriver` 在 master 上并无字面类——执行图
的真实驱动面是 `app/services/workflow_runtime/`（`machine.py` 纯函数 +
`NodeState` 词表 + V5 Driver/InstanceStore）；子代理委派先例
`SubagentDispatcher`（ADR-0104）/`subagent_roles`（ADR-0101）已在产。本 PR
**不新造第五/六套 DAG、不做第二 Agent 框架**（对齐 ADR-0184 D1 与
subagent.py 既有不变式），只补四个真实缺口（S1-S4）：

1. **DAG 级专业分派（S1）**：`SwarmOrchestrator` 把复合空间任务（如防汛
   选址）经确定性启发式分解器展开为「数据猎手 → 计算专家（叠加/选址）→
   制图专家 → 审计裁判」带依赖子任务图；任务状态与调度数学**直接复用
   Execution Graph**——`NodeState` 词表、`machine.ready_set /
   downstream_closure / check_transition` 纯函数经
   `WorkflowMachineGraphPort` 适配（零新图 IR，ADR-0187 D2）。LLM 分解器
   经 `SwarmTaskDecomposer` Protocol 可插拔（v1 非目标，接口已留）。
2. **集群级并发熔断（S3）**：进程级单例 `SwarmConcurrencyGovernor`
   （每 loop `asyncio.Semaphore(3)` + 活跃台账），**任何**编排器实例派发
   前必须 acquire、`finally` 释放（acquire 取消路径不释放——防超发）。
   5 任务背压用例证明严格 ≤3 在飞 + 槽位释放即流水线唤醒；
   双编排器交叉用例证明上限是集群级而非 run 级；容量竞争按 priority
   降序稳定派发。
3. **提货券凭证总线（S2，Zero Big Data in Context）**：子代理唯一合法
   输入 = 任务切片（≤800 字符）+ `WorldStateProjection`（≤24 短事实、
   refs-only、validator 对非 `ref:` 值 fail-closed）+ 上游券摘要；唯一
   合法回传 = `SubagentReceipt`（produced_refs ≤12 强制 `ref:` 前缀、
   摘要截断 400/300）。`normalize_receipt` 二次归一化：剔除载荷走私、
   期望产出零券诚实降级（non_compliant）。聚合经 `SwarmAssetManifest`
   验券（`ref_exists` probe，缺失剔除披露）→ 会话锁内 upsert
   `CapabilityProgress` + manifest 全量落 session store（`prefix="swarm"`），
   计划信封内只有提货券（ADR-0180「plan payload 永不内联」对齐）。
   回写全程 fail-open。
4. **受保护网关（S4）**：`agent_pi_bridge.SwarmBridge` ——
   `GIS_SWARM_ORCHESTRATOR` 默认关（关闭路径零行为漂移、agent_swarm 零
   加载，全惰性 import）+ 主 turn 互斥（`_active_turns` 在飞即拒）+
   goal 有界（>2000 字符拒）+ 有界输出（run_id/终态/计数/refs，零
   payload）。

失败隔离语义：每任务独立重试（attempts ≤3，与 V5 `MAX_NODE_ATTEMPTS`
对齐；仅 retryable/timeout 自动重试）；**destructive at-most-once**
（ADR-0184 D6 同款，重试预算充裕也不自动重驱）；单点 FAILED → 仅传递
下游闭包 SKIPPED（`machine.downstream_closure`），旁支照常流水；
optional 任务重试穷尽走降级路线（FAILED→SKIPPED 两步合法转移 +
degraded 券披露 + 集群 partial 终态）；launcher 体内全量兜底，集群
`run_swarm` 绝不因子任务异常退出。

## 基线与 Phase 0 复核

- 执行时基线：origin/master `3eb2cc6a`（Merge PR #1292）；分支创建后
  master 未再移动（提交 PR 前已 re-fetch 核对）。
- worktree 隔离：`webgis-wt-agent-03`（分支
  `agent/03-specialist-subagent-swarm-orchestrator`）。
- 与既有体系防重复：`swarm` 全仓无代码重名（仅历史调研文档提及）；
  `orchestrator` 一词已被 plan/repair 语义占用，本线类名统一 `Swarm*`
  前缀避让；subagent 车道既有 ADR-0104/0101 实现全部复用不重建
  （`SubagentDispatcherRuntime` 生产适配器包装 `SubagentDispatcher.run`，
  角色映射 `subagent_roles`，未知角色降级披露）。
- 环境注记：仓库 flat-layout 多顶层包导致 `pip install -e .` 不可用
  （master 同况，非本线引入）；测试依赖 `pytest.ini pythonpath = .`，
  开发环境按 requirements-dev 安装。

## 架构 before/after

before（复合空间任务）：单 Agent（Pi）单线程长会话串行包办——上下文
线性膨胀、长耗时步骤锁死对话通道、异常沿串行链路传播、并发上限只有
`run_parallel` 的扁平扇出（无依赖拓扑表达）。

after（委派链路）：

```
Pi Master（宿主不变）
  └─ SwarmBridge（开关/主turn互斥/有界goal → 通过才惰性加载 agent_swarm）
       └─ SwarmOrchestrator.run_swarm(root_goal)
            ├─ HeuristicSpatialDecomposer → validate_swarm_graph（Kahn 判环）
            ├─ WorkflowMachineGraphPort（ready_set/闭包/转移 = machine 纯函数）
            ├─ per-task launcher ──► SwarmConcurrencyGovernor（全局 ≤3）
            │      └─ SpecialistDispatcher → SpecialistRuntime
            │            （生产 = SubagentDispatcher.run；测试 = 脚本假件）
            ├─ 失败隔离：重试/降级(SKIPPED结算)/下游闭包跳过
            └─ SwarmAggregator：验券 → Manifest →（fail-open）
                  SessionPlanSwarmSink（会话锁内 CapabilityProgress + ref 提货券）
```

## 测试证据（本地）

- 新增 `tests/unit/test_swarm_orchestrator.py` **28 tests 全绿**（离线
  确定性：脚本假 runtime + asyncio.Event 门控，无 LLM/无网络/无真 DB）：
  契约边界 4（非 ref 拒绝/截断/依赖上限）、DAG 分解 3（防汛选址拓扑/
  通用四相位回退/环与悬空依赖拒绝）、并发 4（≤3 背压+唤醒/跨编排器
  全局上限/依赖序+上游券注入/优先级）、失败隔离 6（下游闭包/瞬时重试/
  optional 降级/destructive 不重驱/超时熔断/取消收敛）、提货券与聚合 6、
  网关守卫 4、快照 1。
- 受影响面回归全绿：agent_pi_bridge / subagent 相关既有套件
  **66 passed**（audit-fixes / audit4-harness / execution-stability-v3 /
  fail-closed-cartography-review / finalize-display-evidence /
  test_subagent / test_subagent_roles_v2）；相邻依赖面 workflow_runtime
  machine/contracts + harness_kernel **54 passed**。
- `ruff check`（CI 同口径规则集 E4/E7/E9/F）对全部改动文件 **0 finding**。
- `git diff origin/master...HEAD`：10 文件（+3036 行：5 app + 2 docs +
  1 tests + 1 review + 1 本 pr-body），无 frontend/TS 面，无生成物。

## 开发过程缺陷记录（TDD 抓出并修复，详见 review/AGENT-03-REVIEW.md §5）

- **[P0] 调度主循环空转**：已结算 launcher 未移出等待集，
  `asyncio.wait(FIRST_COMPLETED)` 每轮立即返回同一批 done，循环空转至
  迭代上限后触发收尾清扫误取消全部在飞任务。修复：消费即按 identity
  pop。由背压唤醒用例暴露。
- **[P1] acquire 取消路径信号量超发**：launcher 在 acquire 上被取消时
  finally 仍无条件 release，破坏集群上限。修复：acquired 标记守卫。
- **[P1] optional 降级路线缺失**：重试穷尽未转降级券。修复：`_settle`
  转换 DEGRADED（SKIPPED 结算 + partial + failed_capabilities 披露）。
- **[P2] aggregator 惰性导入作用域 NameError**：显式传参修复。

## 已知边界（ADR-0187 D8 明确非目标）

- 集群运行态为进程内有界对象：pod 重启丢失在飞 swarm（产物 ref 已落
  session store，可扫券重建）；
- SwarmBridge 主 turn 互斥查进程内表，跨 pod 互斥留 Redis
  pi_turn_registry 贯通时升级；
- 与 V5 DB 账本贯通（swarm 子任务 ↔ V5 实例节点）留 K 级后续，接缝由
  `WorkflowMachineGraphPort` 与提货券通道预留。
