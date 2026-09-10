"""GeoCompute Cluster Runtime V6 契约（wave 1）。

集群控制平面的**纯类型层**：run 级状态机、lease/epoch fencing、worker 能力、
资源声明。这里没有任何 I/O —— 持久化在 ``cluster.store``，调度在
``cluster.scheduler``。

设计不变式（.agent-work/geocompute-v6/01-architecture.md）：
- run 生命周期真相是 ``geocompute_runs`` 表（此前 run 无持久行，是新事实域，
  不与 ``analysis_tasks``（节点 job 真相）/ ``geocompute_run_evidence``（终态
  证据）重复 —— 三者各管一列真相，互不复制）；
- 状态转移必须经 ``TRANSITIONS`` 白名单 + epoch CAS（store 层执行）；
  terminal 状态没有出边，重复转移 = 幂等 no-op；
- PREEMPTED 是**可驻留**状态（客户端可见「被抢占、等待重排」），再次派发
  经 LEASED（新 lease/epoch），绝不原地 RUNNING。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field


class ClusterRunStatus(str, Enum):
    """run 级持久状态机（geocompute_runs.status 词表）。

    与 ``plan.ExecutionRunStatus``（进程内 run 摘要）对齐的部分同名同值；
    ``queued/leased/preempted`` 是集群控制面新增的可驻留状态。
    """

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PREEMPTED = "preempted"


#: terminal：终态没有出边（CAS 层强制；这里是唯一词表真相）。
TERMINAL_STATUSES: frozenset[ClusterRunStatus] = frozenset({
    ClusterRunStatus.COMPLETED,
    ClusterRunStatus.FAILED,
    ClusterRunStatus.CANCELLED,
})

#: 可派发（coordinator pick 的候选状态）。PREEMPTED 回到派发队列的**队尾**
#: （由 fairness 的 submit seq 排序保证 —— 抢占者先走）。
DISPATCHABLE_STATUSES: frozenset[ClusterRunStatus] = frozenset({
    ClusterRunStatus.QUEUED,
    ClusterRunStatus.PREEMPTED,
})

#: 占用 lease 的状态（reclaim 扫描的候选）。
LEASED_STATUSES: frozenset[ClusterRunStatus] = frozenset({
    ClusterRunStatus.LEASED,
    ClusterRunStatus.RUNNING,
})

#: 状态转移白名单。key = from；value = 允许的 to 集合。
#: - QUEUED → LEASED：认领（epoch++，attempts++）
#: - LEASED → RUNNING：执行体启动；→ QUEUED：reclaim（执行未启动即过期）
#: - RUNNING → PREEMPTED：安全点协作让出；→ QUEUED：reclaim（执行中丢失 lease）
#: - PREEMPTED → LEASED（重派）/ CANCELLED
#: - 任意非 terminal → CANCELLED（取消旗标路径）
_TRANSITION_MAP: Dict[ClusterRunStatus, frozenset[ClusterRunStatus]] = {
    ClusterRunStatus.QUEUED: frozenset({
        ClusterRunStatus.LEASED, ClusterRunStatus.CANCELLED,
    }),
    ClusterRunStatus.LEASED: frozenset({
        ClusterRunStatus.RUNNING, ClusterRunStatus.QUEUED,
        ClusterRunStatus.FAILED, ClusterRunStatus.CANCELLED,
    }),
    ClusterRunStatus.RUNNING: frozenset({
        ClusterRunStatus.COMPLETED, ClusterRunStatus.FAILED,
        ClusterRunStatus.CANCELLED, ClusterRunStatus.QUEUED,
        ClusterRunStatus.PREEMPTED,
    }),
    ClusterRunStatus.PREEMPTED: frozenset({
        ClusterRunStatus.LEASED, ClusterRunStatus.CANCELLED,
    }),
    # terminal：无出边。
    ClusterRunStatus.COMPLETED: frozenset(),
    ClusterRunStatus.FAILED: frozenset(),
    ClusterRunStatus.CANCELLED: frozenset(),
}

#: 只读投影（store/scheduler 消费；避免外部改写白名单）。
TRANSITIONS: Dict[ClusterRunStatus, frozenset[ClusterRunStatus]] = {
    k: frozenset(v) for k, v in _TRANSITION_MAP.items()
}


def transition_allowed(current: ClusterRunStatus, to: ClusterRunStatus) -> bool:
    """状态转移合法性（纯函数；store 的 CAS WHERE 子句由此派生）。"""
    return to in TRANSITIONS.get(current, frozenset())


def is_terminal(status: ClusterRunStatus | str) -> bool:
    try:
        return ClusterRunStatus(status) in TERMINAL_STATUSES
    except ValueError:
        return False


class RunPriority:
    """优先级词表（整数可比；submit 可选，默认 NORMAL）。

    有界词表而非自由整数：防止「优先级通胀」让抢占退化为噪声。
    """

    LOW = 0
    NORMAL = 5
    HIGH = 10

    ALL = (LOW, NORMAL, HIGH)

    @classmethod
    def coerce(cls, value) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            return cls.NORMAL
        return v if v in cls.ALL else cls.NORMAL


class RunLease(BaseModel):
    """run lease（fencing 凭证；只读投影，真相在 run 行）。

    ``epoch`` 从 1 起每次认领递增 —— 所有写路径（心跳/转移/终态）必须带
    自己 observed 的 epoch，store 层 CAS 不匹配即拒绝：旧 coordinator 复活
    后无法覆盖新 attempt（split-brain 防护）。
    """

    run_id: str
    coordinator_id: str = Field(min_length=1, max_length=128)
    epoch: int = Field(ge=1)
    expires_at_epoch_s: float = Field(gt=0, description="time.time() 域的过期时刻")
    ttl_s: float = Field(gt=0)


class WorkerCapability(BaseModel):
    """worker/coordinator 能力声明（geocompute_workers 行的契约投影）。

    ``profiles``：该 worker 消费的 profile 队列 → 并行槽位数（{"raster": 1}）。
    coordinator 的 ``profiles`` 为空 dict（它执行 in_process 编排，不直接
    消费 profile 队列 —— durable 节点仍经 broker 派发给 worker）。
    """

    worker_id: str = Field(min_length=1, max_length=128)
    role: str = Field(default="worker", pattern="^(coordinator|worker)$")
    profiles: Dict[str, int] = Field(default_factory=dict)
    info: Dict[str, str] = Field(default_factory=dict, max_length=16)

    @property
    def covered_profiles(self) -> frozenset[str]:
        return frozenset(k for k, v in self.profiles.items() if v > 0)


class ResourceClaim(BaseModel):
    """run 级资源声明（集群账本 ``geocompute_resource_usage`` 的记账单位）。

    粒度是 run：认领时 reserve（估计值，上限钳制），终态/reclaim 时精确
    release（同一事务，CAS 保证 exactly-once）。节点级记账仍是进程内 L1
    governor（budgets.ResourceGovernor）—— 两层各管一个爆炸半径。

    V8 新增维度（additive；缺省 0 = V7 语义逐字节兼容）：
    - ``mem_mb``：run 估计峰值内存（节点 estimate.memory_mb 之和，钳上界），
      enforcing 账本以此做「先预留后启动」的 OOM 预防；
    - ``gpu``：run 声明的 GPU 卡数（ResourceRequest.gpu），计数级预留 ——
      防 N 个 GPU run 叠加超卖同一池卡。
    """

    scope_key: str = Field(min_length=1, max_length=80)
    rows: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)
    units: int = Field(default=0, ge=0, description="并发槽位单位（重节点 2，复用 slot_units_for 语义）")
    mem_mb: int = Field(default=0, ge=0, description="估计峰值内存（MiB）；0 = 无估计/不预留")
    gpu: int = Field(default=0, ge=0, le=64, description="GPU 卡数计数预留")


class ResourceRequest(BaseModel):
    """run 级资源 envelope（V7 cluster submit 可选字段；placement 准入依据）。

    语义（01-architecture.md §2.2，round1 #3 修订后的三层放置）：
    - 全部字段有服务端上界 —— 客户端不得自授无界 GPU/内存（非目标声明）；
    - ``gpu > 0`` → 只有 GPU worker 合格（run 级准入 gating 是硬约束；
      节点级由 worker 侧准入守卫有界收敛，placement 承诺分层声明）；
    - ``required_profiles`` 在 submit 端与 durable 节点自动派生集做 **union**
      （用户只能加宽不能收窄 —— 收窄会把「安全留队」劣化为必然 WORKER_LOSS）。
    """

    model_config = {"extra": "forbid"}

    min_mem_mb: int = Field(default=0, ge=0, le=2_097_152)
    min_cpu: int = Field(default=0, ge=0, le=1024)
    gpu: int = Field(default=0, ge=0, le=8)
    #: charset 白名单（round2 Rn5：与架构 §5 词表声明一致；当前仅等值
    #: 比较，无注入面，白名单是纵深防御）
    zone: Optional[str] = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    required_profiles: list[str] = Field(default_factory=list, max_length=8)
    #: V8：CUDA 不可用回退。True 且 GPU run 等待超过 fallback 等待窗
    #: （scheduler 层计时）→ 剥离 gpu 要求改派 CPU worker（``gpu_fallback``
    #: 事件，诚实可见）。False（默认）= V7 语义：无限期留队等待 GPU worker。
    fallback_cpu: bool = False

    def normalized(self) -> "ResourceRequest":
        """词表过滤后的规范投影（非法 profile 词在 submit 端 422，这里兜底）。"""
        from app.services.geocompute.durable import EXECUTION_QUEUE_PROFILES

        return ResourceRequest(
            min_mem_mb=self.min_mem_mb,
            min_cpu=self.min_cpu,
            gpu=self.gpu,
            zone=(self.zone or None),
            required_profiles=sorted(
                {p for p in self.required_profiles
                 if p in EXECUTION_QUEUE_PROFILES}
            ),
            fallback_cpu=self.fallback_cpu,
        )


#: plan 快照落库上界（防止 DB 行膨胀 DoS；typed 413 拒绝）。
MAX_PLAN_SNAPSHOT_BYTES = 256 * 1024

#: run 级重试上界（attempts = lease **丢失**（reclaim）次数；第 max 次丢失
#: 即 failed[WORKER_LOSS] —— 总执行次数 ≤ 1 + (max-1) 次恢复重试）。
DEFAULT_MAX_RUN_ATTEMPTS = 3

#: 抢占次数安全上界（livelock 保险丝；超过 → failed[PREEMPT_EXHAUSTED]）。
MAX_PREEMPTS = 64

#: V8 账本拒绝维度词表（enforcing reserve 拒绝时定位维度）。
#: ``waiting_resource`` 事件的 status 投影 = f"resource:{dim}"（≤20 字符）。
RESOURCE_DIMENSIONS: tuple[str, ...] = (
    "rows", "bytes", "units", "mem_mb", "gpu",
)


def run_error_for_reclaim(attempts: int, max_attempts: int) -> Optional[str]:
    """reclaim 时的 run 级 error_code：attempt 耗尽 → WORKER_LOSS，否则 None（回队重试）。"""
    if attempts >= max_attempts:
        return "WORKER_LOSS"
    return None
