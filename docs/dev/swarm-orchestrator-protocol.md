# Swarm Orchestrator 协议规范（specialist-subagent-swarm v1）

- 状态：Active（随 ADR-0187 落地）
- 分支：`agent/03-specialist-subagent-swarm-orchestrator`
- 基线：master @ 3eb2cc6a
- 关联：ADR-0187（架构决策）、ADR-0184（Execution Graph）、ADR-0180
  （Harness Kernel / SessionPlan）、ADR-0104/0101（Subagent 委派与角色）

本文件是 `app/services/agent_swarm/` 的对外协议契约：数据模型、状态机、
有界纪律、派发契约、聚合总线、网关守卫与测试指南。实现与本文冲突时，
先改本文再改码。

## 0. 模块布局与依赖方向

```
app/services/agent_swarm/
├── __init__.py      # PEP 562 惰性导出（harness_kernel 同款，防循环导入）
├── contracts.py     # 叶子契约：Pydantic 模型 + 有界常量 + 词表
├── orchestrator.py  # SwarmOrchestrator + 启发式分解器 + 全局并发 Governor
├── dispatcher.py    # SpecialistDispatcher + SpecialistRuntime 协议 + 生产适配器
└── aggregator.py    # SwarmAggregator + SwarmAssetManifest + SessionPlanSwarmSink
```

依赖方向（红线，违者 review 打回）：

```
agent_pi_bridge ──► agent_swarm.orchestrator ──► agent_swarm.contracts
        │                    │    ▲
        │                    ▼    │（Protocol 注入，不反向 import）
        │             agent_swarm.dispatcher / aggregator
        ▼
agent_swarm.contracts ──► workflow_runtime.contracts（仅 NodeState 词表）
agent_swarm.orchestrator ──► workflow_runtime.machine（仅纯函数）
```

`contracts.py` 只依赖 stdlib + pydantic + `workflow_runtime.contracts`；
其余模块对 `session_plan` / `subagent` / `subagent_roles` 一律**函数内惰性
import**，保证开关关闭路径零加载。

## 1. 数据契约（contracts.py）

### 1.1 词表

| 词表 | 取值 | 说明 |
|---|---|---|
| `SwarmSpecialistRole` | `data_hunter` / `compute_specialist` / `cartography_specialist` / `audit_judge` | 四类专业子代理（数据猎手/计算专家/制图专家/审计裁判） |
| 任务状态 | 复用 `workflow_runtime.contracts.NodeState` | PENDING/READY/RUNNING/SUCCEEDED/FAILED/SKIPPED/CANCELLED；本层不用 STALE/BLOCKED |
| `SwarmReceiptStatus` | `succeeded` / `failed` / `degraded` | 提货券终态；`degraded` 落节点态为 SKIPPED |
| `SwarmRunState` | `running` / `succeeded` / `partial` / `failed` / `cancelled` | 集群运行终态裁决 |
| `SwarmErrorCode` | `retryable` / `non_retryable` / `timeout` / `cancelled` | 自动重试仅认前两者 + timeout |
| `side_effect` | `pure` / `derived_external` / `destructive` | 沿用 ADR-0184 D6；destructive 禁自动重试 |

### 1.2 有界纪律（全部 Pydantic 层强制）

| 常量 | 值 | 约束对象 |
|---|---|---|
| `MAX_SWARM_CONCURRENCY` | 3 | 集群全局在飞子任务（Governor 熔断） |
| `MAX_SWARM_TASKS` | 12 | 单次集群任务数 |
| `MAX_DEPENDS_PER_TASK` | 6 | 单任务依赖数 |
| `MAX_RETRIES_PER_TASK` | 2 | 自动重试次数（attempts ≤ 3） |
| `DEFAULT_TASK_TIMEOUT_S` | 120.0 | 单任务硬墙钟 |
| `MAX_GOAL_CHARS` / `MAX_TASK_GOAL_CHARS` | 2000 / 800 | 根目标 / 任务切片 goal |
| `MAX_SUMMARY_CHARS` / `MAX_ERROR_CHARS` | 400 / 300 | receipt 摘要（超长截断） |
| `MAX_REFS_PER_RECEIPT` | 12 | 提货券 ref 数 |
| `MAX_FACTS` / `MAX_FACT_CHARS` | 24 / 240 | 世界态投影事实行 |
| `MAX_REF_LIST` / `MAX_CONSTRAINTS` | 24 / 12 | 投影相关 ref / 约束行 |
| `MAX_MANIFEST_ENTRIES` | 32 | 聚合清单条目 |

### 1.3 模型

```python
class WorldStateProjection(BaseModel):
    session_id: str
    goal_summary: str = ""                 # ≤ MAX_GOAL_CHARS
    facts: list[str] = []                  # ≤24 条，每条 ≤240 字符（截断）
    relevant_refs: list[str] = []          # ≤24 个，必须 "ref:" 前缀（fail-closed）
    constraints: list[str] = []            # ≤12 条
    world_revision: str = ""

class SwarmTaskDescriptor(BaseModel):
    task_id: str                           # ^[a-z0-9_.-]{1,64}$
    goal: str                              # ≤800 字符
    role: SwarmSpecialistRole
    capability: str = ""                   # 回写 SessionPlan 的 capability 行
    depends_on: tuple[str, ...] = ()       # ≤6，必须引用已声明 task_id
    expected_outputs: tuple[str, ...] = () # 期望产物 ref slug 提示
    optional: bool = False                 # True → 失败走降级不击穿
    side_effect: str = "pure"
    priority: int = 5                      # [-10,10]，大者先派
    timeout_s: float = 120.0
    max_retries: int = 1
    # 运行期回填（非输入）：
    state: str = "PENDING"; attempts: int = 0; degraded: bool = False

class SpecialistAssignment(BaseModel):
    assignment_id: str                     # asg-{task_id}-{uuid8}
    task: SwarmTaskDescriptor
    projection: WorldStateProjection       # 输入切片②
    upstream_receipts: list[SubagentReceipt]  # 直接上游提货券摘要（输入切片③）
    issued_at: float

class SubagentReceipt(BaseModel):
    assignment_id: str; task_id: str; role: SwarmSpecialistRole
    status: SwarmReceiptStatus
    produced_refs: list[str] = []          # ≤12，必须 "ref:" 前缀（fail-closed）
    summary: str = ""; error: str = ""     # 截断至 400/300
    error_code: str = ""; degraded: bool = False
    attempts: int = 1; heartbeats: int = 0
    wall_time_s: float = 0.0; finished_at: float = 0.0

class SwarmAssetEntry / SwarmAssetManifest:
    ref_id/capability/role/task_id/summary ≤32 条 + failed_capabilities

class SwarmExecutionStatus:
    run_id/session_id/root_goal/state/counts(按 NodeState)/
    active_task_ids/manifest/manifest_ref/started_at/finished_at
```

**Zero Big Data in Context 强制点**：`relevant_refs`/`produced_refs`
非 `ref:` 前缀 → `ValidationError`（fail-closed，不做静默转换——在契约
边界就该炸）；`facts`/`summary`/`error` 超长 → 截断（可用性优先）；
dispatcher `normalize_receipt` 对 runtime 返回值二次归一化（防运行时
实现绕过契约）。

## 2. 状态机（复用 V5 裁决）

```
PENDING ──► READY ──► RUNNING ──► SUCCEEDED
   │          │          ├──► FAILED ──(重试预算内/retryable)──► RUNNING
   │          │          │        └──► FAILED（终态）→ 下游闭包 SKIPPED
   │          │          └──► CANCELLED（集群 cancel / 超时穷尽）
   └──► SKIPPED（上游 FAILED 闭包传播 / 收尾不可达清扫）
降级路线：optional 任务 FAILED → receipt.status=degraded → 节点 SKIPPED
```

- 每次转移经 `machine.check_transition` 合法性门（非法转移抛
  `SwarmContractError`，教训即缺陷）；
- 就绪集 = `machine.ready_set(dag, states)`：上游全 ∈ {SUCCEEDED, SKIPPED}
  才可派发——降级（SKIPPED）对下游即已结算，与 V5 R1-C1 同波防护一致；
- 失败传播 = `machine.downstream_closure(dag, {failed_id})`，仅
  PENDING/READY 态成员转 SKIPPED；收尾阶段不可达残留 PENDING 一并
  SKIPPED 清扫（DAG 保证无死锁）。

## 3. 派发契约（dispatcher.py）

```python
class SpecialistRuntime(Protocol):
    async def execute(self, assignment: SpecialistAssignment, *,
                      on_heartbeat: Callable[[str], None] | None = None,
                      ) -> SubagentReceipt: ...

class SpecialistDispatcher:
    def __init__(self, runtime: SpecialistRuntime): ...
    async def dispatch(self, assignment, *, on_heartbeat=None) -> SubagentReceipt:
        # runtime 返回 → normalize_receipt 归一化（词表合法化/截断/ref 纪律/
        # expected_outputs 零产 出降级），任何 runtime 异常折算 FAILED 券
```

- 测试/脚本注入：`ScriptedRuntime`（tests）或任意实现 Protocol 的假件；
- **生产适配器** `SubagentDispatcherRuntime`：惰性包装
  `SubagentDispatcher(registry, parent_session_id).run(task=切片,
  role=角色映射)`；`SubagentResult → SubagentReceipt` 映射：
  `success=True → succeeded`；`success=False → failed(non_retryable)`；
  refs 直通（非 `ref:` 项在 normalize 层剔除并降级）。
- 心跳：runtime 可在长任务中调用 `on_heartbeat(note)`；v1 仅计数与记末次
  备注（观测面），不参与超时判定（`timeout_s` 为硬墙钟）。

## 4. 并发熔断（orchestrator.SwarmConcurrencyGovernor）

- 进程单例 `get_swarm_concurrency_governor()`；每 event-loop 一把
  `asyncio.Semaphore(MAX_SWARM_CONCURRENCY=3)`（loop 更换自动重建并清
  陈旧台账——测试隔离需要）；
- 派发前 `await acquire(assignment_id, task_id)`（在信号量上排队 =
  队列背压，任务态诚实停 READY）；`finally release()`；
- `snapshot() -> {max_concurrency, active_count, active_task_ids}` 供
  巡检/测试断言集群在飞数；
- 调度排序：`(-priority, task_id)` 稳定序；同波多就绪一次全部 spawn
  launcher（排队在信号量上），完成即唤醒队首（流水线自续）。

## 5. 分解契约（orchestrator.HeuristicSpatialDecomposer）

`SwarmTaskDecomposer` Protocol：`decompose(root_goal, projection) ->
list[SwarmTaskDescriptor]`。实现方负责输出**可验证合法**的图（唯一 id、
依赖存在、无环、≤上限）；`SwarmOrchestrator` 开跑前用
`validate_swarm_graph` 复检（fail-closed，Kahn 判环）。

v1 启发式（确定性，中英关键词，大小写不敏感）：

| 相位 | 触发词（例） | 产出任务 | 依赖 |
|---|---|---|---|
| 数据获取 | 数据/获取/边界/DEM/水文/降雨/POI/data/fetch | `swarm.data.base_geo`；命中专题词（水文/降雨/POI…）再产 `swarm.data.theme` | — |
| 分析计算 | 分析/计算/评估/叠加/统计/analysis/compute | `swarm.compute.overlay` | 全部数据任务 |
| 选址适宜性 | 选址/适宜性/suitability/siting | `swarm.compute.siting` | `swarm.compute.overlay` |
| 制图排版 | 制图/地图/专题图/出图/排版/map/chart | `swarm.cartography.compose` | 最末计算任务 |
| 质量审计 | 总是产出 | `swarm.audit.judge` | 制图 + 首个计算任务 |

零命中时退化为通用四相位管道（data→compute→cartography→audit）。
`audit_judge` 任务 `optional=True`（审计缺位不阻断成果交付，降级披露）。

## 6. 聚合总线（aggregator.py）

```python
class PlanSink(Protocol):
    async def merge(self, manifest: SwarmAssetManifest) -> Optional[str]: ...

class SwarmAggregator:
    def collect(receipts, tasks, *, run_id, session_id) -> SwarmAssetManifest
    async def merge(manifest) -> Optional[str]   # fail-open，异常返回 None
```

- `collect`：succeeded+degraded 券的 `produced_refs` → 条目；FAILED 任务
  capability → `failed_capabilities`；注入 store 时逐 ref 验券
  （`ref_exists`），缺失剔除并披露；
- 默认 sink `SessionPlanSwarmSink`：
  `session_lock_registry.lock(sid, fail_on_degraded=True)` 内
  `load_session_plan` →（缺则新建最小信封）→ upsert
  `CapabilityProgress(capability, status="complete", bound_ref=首产 ref)`
  → `save_session_plan`（revision 自增）；manifest 全量
  `store.store(sid, payload, prefix="swarm")` 得 `manifest_ref`；
- **fail-open**：回写任何异常只记日志，绝不改变集群 settle 结果。

## 7. 网关守卫（agent_pi_bridge.SwarmBridge）

```python
class SwarmBridge:
    @classmethod
    def enabled(cls) -> bool        # env GIS_SWARM_ORCHESTRATOR，默认关
    async def delegate_compound_task(self, root_goal, *, cartography_context=None) -> dict
def get_swarm_bridge(session_id) -> SwarmBridge
```

守卫序（任一不满足 → `{"delegated": False, "reason": ...}` 诚实拒绝）：

1. `swarm_disabled`：特性开关关闭（默认态，零行为漂移）；
2. `invalid_goal`：空/超 2000 字符；
3. `master_turn_active`：`_active_turns` 中该 session 有在飞 Pi turn
   （与主对话通道互斥，防并发写会话态）；
4. 通过 → 惰性构建 `WorldStateProjection`（fail-open 最小切片：goal 摘要
   + cartography_context 摘要行）→ `run_swarm` → 返回有界摘要
   `{delegated, run_id, state, counts, refs, manifest_ref}`。

## 8. 使用示例

```python
from app.services.agent_swarm import SwarmOrchestrator, WorldStateProjection
from app.services.agent_swarm.dispatcher import SpecialistDispatcher, SubagentDispatcherRuntime

orch = SwarmOrchestrator(
    session_id,
    dispatcher=SpecialistDispatcher(SubagentDispatcherRuntime(session_id)),
)
status = await orch.run_swarm("在 XX 流域开展防汛风险分析并完成选址适宜性评估与专题制图")
status.state          # succeeded / partial / failed / cancelled
status.manifest       # SwarmAssetManifest：全部产物 ref 提货单
status.manifest_ref   # ref:swarm-... （manifest 全量在 session store）
```

## 9. 测试指南（tests/unit/test_swarm_orchestrator.py）

- 全部离线确定性：`ScriptedRuntime`（asyncio.Event 门控 + 行为脚本），
  无 LLM、无网络、无 DB（sink 注入 `FakeStore`）；
- 背压断言：runtime 内进出计数 `max_inflight ≤ 3` 且 ==3； Governor
  `snapshot().active_count` 交叉验证；双编排器并发合并 ≤3（集群级）；
- 失败隔离断言：单点 FAILED → 仅下游闭包 SKIPPED；集群仍 settle；
- 纪律断言：非 ref 产出被拒/降级、destructive 零自动重试、超时熔断；
- 显式 `@pytest.mark.asyncio`；事件门控代替 sleep（轮询上限 2s 墙钟）。
