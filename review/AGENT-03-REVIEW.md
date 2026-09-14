# AGENT-03 代码审查纪要：Specialist Subagent Swarm Orchestrator（ADR-0187）

- 分支：`agent/03-specialist-subagent-swarm-orchestrator`（worktree `webgis-wt-agent-03`）
- 基线：origin/master @ 3eb2cc6a
- 日期：2026-09-14
- 审查对象：`app/services/agent_swarm/`（5 文件）、`app/agent_pi_bridge.py`
  （SwarmBridge 增量）、`tests/unit/test_swarm_orchestrator.py`、
  `docs/adr/0187-*.md`、`docs/dev/swarm-orchestrator-protocol.md`

## 1. 交付物清单

| 文件 | 内容 |
|---|---|
| `docs/adr/0187-specialist-subagent-swarm-orchestrator.md` | 架构决策 D1-D8（拓扑/复用执行图/上下文隔离/并发熔断/失败隔离/聚合总线/网关守卫/非目标） |
| `docs/dev/swarm-orchestrator-protocol.md` | 协议规范：契约、状态机、有界表、派发契约、分解启发式、网关守卫、测试指南 |
| `app/services/agent_swarm/contracts.py` | 叶子契约：SwarmTaskDescriptor / SpecialistAssignment / SubagentReceipt / SwarmAssetManifest / SwarmExecutionStatus + 有界常量 + 词表 |
| `app/services/agent_swarm/orchestrator.py` | SwarmOrchestrator、HeuristicSpatialDecomposer、validate_swarm_graph、SwarmConcurrencyGovernor（全局 ≤3）、WorkflowMachineGraphPort |
| `app/services/agent_swarm/dispatcher.py` | SpecialistRuntime 协议、SpecialistDispatcher、normalize_receipt、SubagentDispatcherRuntime 生产适配器 |
| `app/services/agent_swarm/aggregator.py` | SwarmAggregator、SwarmAssetManifest 聚合、SessionPlanSwarmSink（会话锁内回写）、NullSink |
| `app/services/agent_swarm/__init__.py` | PEP 562 惰性导出面（harness_kernel 同款） |
| `app/agent_pi_bridge.py` | 增量 SwarmBridge（默认关开关 + 主 turn 互斥 + 有界输入输出）+ get_swarm_bridge |
| `tests/unit/test_swarm_orchestrator.py` | 28 用例（DAG 映射 / 背压熔断 / 失败隔离 / 重试降级 / 提货券纪律 / 聚合回写 / 网关守卫） |

## 2. 门禁结果

- `pytest tests/unit/test_swarm_orchestrator.py`：**28 passed**（离线确定性：
  假 runtime + asyncio.Event 门控，无 LLM / 无网络 / 无真 DB）；
- `ruff check app/services/agent_swarm/ tests/unit/test_swarm_orchestrator.py
  app/agent_pi_bridge.py`：**All checks passed**（E4/E7/E9/F 严格档）；
- 回归：agent_pi_bridge / subagent 相关既有套件 **66 passed**
  （test_agent_runtime_audit_fixes / test_audit4_harness_semantics /
  test_execution_stability_v3 / test_fail_closed_cartography_review /
  test_finalize_display_evidence / test_subagent / test_subagent_roles_v2）。

## 3. 安全纪律核查（任务书两大红线）

### 3.1 「Zero Big Data in Context」—— 通过，双层强制

- **入口（输入切片）**：子代理唯一合法输入 `SpecialistAssignment` =
  任务切片（≤800 字符）+ `WorldStateProjection`（facts ≤24 条 ×240 字符、
  relevant_refs ≤24、constraints ≤12）+ 上游券摘要（≤6 张）。不含父目标
  全文，不含对话历史（`contracts.py` 有界常量表逐项可查）。
- **出口（提货券）**：`SubagentReceipt.produced_refs` ≤12 且强制 `ref:`
  前缀；summary/error 截断 400/300。**payload 永不过境**。
- **强制点①**：`WorldStateProjection._refs_only` /
  `SubagentReceipt._refs_only` validator 对非 `ref:` 值直接
  ValidationError（fail-closed，测试
  `test_projection_rejects_non_ref_strings` /
  `test_receipt_rejects_non_ref_outputs_and_truncates_summary`）。
- **强制点②**：`dispatcher.normalize_receipt` 对 runtime 返回值二次归一化
  ——剔除走私载荷、截断摘要、期望产出零券诚实降级（测试
  `test_normalize_receipt_strips_payload_and_enforces_ref_discipline`）。
- **落库面**：manifest 全量入 session store（`prefix="swarm"`），计划信封
  内只有 `CapabilityProgress.bound_ref` 提货券，与 ADR-0180
  「plan payload 永不内联」纪律对齐。

### 3.2 「Subagents ≤ 3」—— 通过，集群级硬界

- `SwarmConcurrencyGovernor` 进程级单例 + 每 loop `asyncio.Semaphore(3)`
  + 活跃台账；**任何**编排器实例派发前必须 acquire，`finally` 释放
  （且仅在实际获取后释放 —— 防 acquire 取消路径的超发）。
- 测试证据：
  - `test_concurrency_capped_at_three_with_pipeline_wake`：5 任务严格 ≤3
    在飞（runtime 计数器 + governor snapshot 双重断言），槽位释放即
    流水线唤醒，排队任务自动补位；
  - `test_cluster_governor_is_global_across_orchestrators`：**双编排器
    交叉派发**全局仍 ≤3（熔断是进程级而非 run 级）；
  - `test_priority_orders_dispatch_under_backpressure`：容量竞争按
    priority 降序派发。
- 生产路径（SubagentDispatcherRuntime）不受本上限豁免 —— 每个子代理
  执行体同样经过 Governor（编排器统一在 acquire 后才调用 dispatch）。

## 4. 架构纪律核查（与 ADR/既有体系的一致性）

- **不新造图 IR（D2）**：任务状态直接复用
  `workflow_runtime.contracts.NodeState`；就绪集/下游闭包/转移合法性全部
  经 `WorkflowMachineGraphPort` 委托 `workflow_runtime.machine` 纯函数；
  降级不是新状态（FAILED→SKIPPED 两步合法转移 + receipt.degraded 披露）；
  转移前 `machine.check_transition` 把关，非法即 SwarmContractError。
- **Pi 是唯一宿主（D1，沿用 ADR-0180/0104）**：SwarmBridge 是受保护
  网关而非第二 Agent 框架；生产 runtime 适配器包装既有
  `SubagentDispatcher.run`（ADR-0104），角色映射 `subagent_roles`
  （ADR-0101），未知角色降级披露；递归与预算语义由既有层继续管辖。
- **一锁两段一落盘（沿用 ADR-0076/0180）**：SessionPlanSwarmSink 在
  `session_lock_registry.lock(fail_on_degraded=True)` 内
  load→upsert→save；回写 fail-open（测试
  `test_aggregator_fail_open_on_sink_error` 证明回写炸了集群照样 settle）。
- **destructive at-most-once（沿用 ADR-0184 D6）**：测试
  `test_destructive_task_never_auto_retried` 断言重试预算充裕时依然
  恰好执行一次。
- **开关关闭零漂移（D7）**：`GIS_SWARM_ORCHESTRATOR` 默认关；关闭时
  `delegate_compound_task` 直接诚实拒绝，agent_swarm 模块零加载（全惰性
  import），Pi 主通道行为逐字节不变。

## 5. Review 中发现并修复的缺陷（过程记录）

1. **调度主循环空转（P0，调试定位）**：已结算 launcher 未从等待集移除，
   `asyncio.wait(FIRST_COMPLETED)` 每轮立即返回同一批 done，主循环空转
   至迭代上限后触发收尾清扫，把在飞 launcher 全部误CANCELLED。
   修复：`_drive` 中按 identity 消费并 pop 已结算 launcher。
   —— 该缺陷由「背压唤醒」测试暴露，5 任务用例三次内测失败驱动修复，
   修复后 28/28 稳定绿。
2. **acquire 取消路径的信号量超发（P1）**：launcher 在
   `governor.acquire` 上被取消时，`finally` 仍无条件 `release`，会凭空
   释放未获取的槽位，破坏集群上限。修复：`acquired` 标记守卫。
3. **optional 降级路线缺失（P1）**：重试穷尽后未按 ADR D5 转换为降级券。
   修复：`_settle` 对 optional+FAILED 转换 DEGRADED（SKIPPED 结算 +
   partial 终态 + failed_capabilities 披露）。
4. **aggregator 惰性导入作用域（P2）**：`_upsert_progress` 静态方法访问
   不到 merge 内惰性导入的 `CapabilityProgress`（NameError）。修复：显式
   传参。
5. **venv editable 安装不可用（环境）**：仓库 flat-layout 多顶层包导致
   `pip install -e .` 失败（主仓同况）；测试依赖 `pytest.ini
   pythonpath = .`，仅安装 requirements-dev.txt 即可，已记录。

## 6. 已知边界与后续（非阻塞）

- 集群运行态为进程内有界对象（ADR-0187 D8 明确非目标）：pod 重启丢失
  在飞 swarm；产物 ref 已落 session store，可由后续恢复机制扫券重建。
- `SwarmBridge` 主 turn 互斥当前检查进程内 `_active_turns` 表；跨 pod
  互斥（Redis pi_turn_registry）留作贯通时升级点。
- 启发式分解对零命中目标退化为通用四相位管道（确定性优先）；LLM 分解器
  经 `SwarmTaskDecomposer` Protocol 可插拔，接口已预留。
- 与 V5 DB 账本的贯通（swarm 子任务 ↔ V5 实例节点、`record_tool_result`
  回写）留作 K 级后续，接缝由 `WorkflowMachineGraphPort` 与提货券通道预留。

## 7. 结论

**放行**。四阶段（ADR → 协议规范 → TDD → 实现）完整走完，两条安全红线
（Zero Big Data in Context / Subagents ≤3）均有契约层强制 + 测试证据；
对既有体系的复用与侵入面符合 ADR-0184/0180/0104 的既定纪律，回归零破坏。
