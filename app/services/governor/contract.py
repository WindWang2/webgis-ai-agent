"""Harness 资源治理契约（rg.v1，ADR-0182）。

Governor V1 的**唯一契约真相**：估算、需求、预算、预留、用量、裁决六类核心
类型 + 确定性分级。设计红线（决策日志 D4）：

1. **未知绝不等于 0**：每个数值维度携带 ``Certainty`` 分级；``unknown`` 维在
   准入判定按保守地板计，``unavailable`` 维不参与判定但必须留痕原因。
2. **粗而诚实 > 伪精确**：数值一律 range（min/expected/max）+ confidence +
   source + reason；拒绝 ``memory = 712.2831MB`` 式假精度。
3. 封闭词表：subsystem / decision / retry_class 全部 enum，杜绝标签基数失控
   （与 observability budgets 的封闭 key 纪律同源）。
4. 纯数据契约，零 IO、零调度——调度语义在 governor.py，投影在 estimation.py。

与 geocompute ``ResourceGovernor`` 的分界（D1）：rows/bytes/nodes 维的 L1
权威在那棵树；本契约的维度是树里不存在的 harness 维（token/browser/export/
wall-time/external-calls/render-work）+ 树维度的只读投影。
"""
from __future__ import annotations

import itertools
import time
import uuid
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

#: 契约版本（演进时必须 bump 并写迁移说明）
SCHEMA_VERSION = "rg.v1"


class Certainty(str, Enum):
    """一个维度数值的认知级别（spec §6：绝不把 unknown 当 0）。"""

    KNOWN = "known"              # 实测/合约值（如已完成的实际用量）
    ESTIMATED = "estimated"      # evidence-backed 估算（带 confidence/range）
    UNKNOWN = "unknown"          # 无法估算 —— 准入按保守地板计
    UNAVAILABLE = "unavailable"  # 该维对本操作无意义 —— 不参与判定，留痕


class Dimension(str, Enum):
    """数值资源维度（封闭词表；spec §6 全量）。"""

    MEMORY_BYTES = "memory_bytes"
    GPU_MEMORY_BYTES = "gpu_memory_bytes"
    NETWORK_BYTES = "network_bytes"
    STORAGE_BYTES = "storage_bytes"
    FEATURE_COUNT = "feature_count"
    PIXEL_COUNT = "pixel_count"
    CONTEXT_TOKENS = "context_tokens"
    OUTPUT_TOKENS = "output_tokens"
    ESTIMATED_LLM_COST = "estimated_llm_cost"
    WALL_TIME_S = "wall_time_s"
    EXTERNAL_SERVICE_CALLS = "external_service_calls"
    RENDER_WORK_UNITS = "render_work_units"


#: 每个 unknown 维参与准入判定的保守地板（缺省；预算方可按 scope 覆盖）。
#: 语义：估算缺失时按此值计费，宁可误拒不可漏放 —— unknown≠0 的落地形式。
CONSERVATIVE_FLOORS: Dict[Dimension, float] = {
    Dimension.MEMORY_BYTES: 256 * 1024 * 1024,     # 256MiB：DF 单结果硬界同量级
    Dimension.GPU_MEMORY_BYTES: 1 * 1024**3,       # 1GiB：推理批次保守占位
    Dimension.NETWORK_BYTES: 16 * 1024 * 1024,     # 16MiB：DF 结果下界同量级
    Dimension.STORAGE_BYTES: 32 * 1024 * 1024,
    Dimension.FEATURE_COUNT: 50_000,               # DF 默认特征上限同量级
    Dimension.PIXEL_COUNT: 50_000_000,             # 50M px ≈ 7000²，raster_guard 量级
    Dimension.CONTEXT_TOKENS: 8_000,
    Dimension.OUTPUT_TOKENS: 4_096,
    Dimension.ESTIMATED_LLM_COST: 0.0,             # 成本维地板 0（无定价时不虚报）
    Dimension.WALL_TIME_S: 300.0,                  # TOOL_TIMEOUT_S 同量级
    Dimension.EXTERNAL_SERVICE_CALLS: 3.0,
    Dimension.RENDER_WORK_UNITS: 1_000_000.0,
}


class DimValue(BaseModel):
    """一个维度的取值：certainty × range + 证据元数据。"""

    certainty: Certainty = Certainty.UNAVAILABLE
    min: Optional[float] = None
    expected: Optional[float] = None
    max: Optional[float] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source: str = ""    # 证据来源（如 "df.cost_model.v1" / "toolcost:heavy"）
    reason: str = ""    # 机器可读理由（如 "no descriptor for tool X"）

    def adjudged(self, *, conservative_floors: Optional[Dict[Dimension, float]] = None,
                 dim: Optional[Dimension] = None) -> float:
        """准入判定用取值：known→expected；estimated→expected；unknown→保守地板；
        unavailable→0（不参与判定）。

        range 缺失的 estimated 按 max 兜底（宁可高估）。``conservative_floors``
        允许预算方按 scope 覆盖缺省地板（None 项回退 CONSERVATIVE_FLOORS）。
        """
        if self.certainty is Certainty.UNAVAILABLE:
            return 0.0
        if self.certainty is Certainty.UNKNOWN:
            if conservative_floors is not None and dim is not None:
                floor = conservative_floors.get(dim)
                if floor is not None:
                    return float(floor)
            return float(CONSERVATIVE_FLOORS.get(dim, 0.0)) if dim else 0.0
        if self.expected is not None:
            return float(self.expected)
        if self.max is not None:
            return float(self.max)
        if self.min is not None:
            return float(self.min)
        # declared estimated/known 但没有任何数值 —— 视同 unknown 的保守处理
        if dim is not None:
            return float(CONSERVATIVE_FLOORS.get(dim, 0.0))
        return 0.0

    def is_meaningful(self) -> bool:
        return self.certainty is not Certainty.UNAVAILABLE

    @classmethod
    def known(cls, value: float, source: str = "") -> "DimValue":
        return cls(certainty=Certainty.KNOWN, min=float(value),
                   expected=float(value), max=float(value),
                   confidence=1.0, source=source)

    @classmethod
    def estimated(cls, lo: float, exp: float, hi: float, *,
                  confidence: float = 0.6, source: str = "", reason: str = "") -> "DimValue":
        lo, exp, hi = float(lo), float(exp), float(hi)
        if hi < lo:
            hi = lo
        # expected 收进 [lo, hi]（range 三元组必须保持 min<=expected<=max）
        if exp < lo:
            exp = lo
        if exp > hi:
            exp = hi
        return cls(certainty=Certainty.ESTIMATED, min=lo, expected=exp, max=hi,
                   confidence=max(0.0, min(1.0, float(confidence))),
                   source=source, reason=reason)

    @classmethod
    def unknown(cls, reason: str) -> "DimValue":
        return cls(certainty=Certainty.UNKNOWN, reason=reason)

    @classmethod
    def unavailable(cls, reason: str = "dimension not applicable") -> "DimValue":
        return cls(certainty=Certainty.UNAVAILABLE, reason=reason)


class RasterWindow(BaseModel):
    """raster 窗口形状（spec §6 raster_window；像素数可导出）。"""

    width: int = Field(ge=0)
    height: int = Field(ge=0)
    bands: int = Field(default=1, ge=0)

    @property
    def pixels(self) -> int:
        return self.width * self.height * max(1, self.bands)


class Subsystem(str, Enum):
    """封闭子系统词表（recon §1 矩阵的行 → 治理对象）。"""

    LLM_CONTEXT = "llm_context"
    PI_TURN = "pi_turn"
    TOOL_DISPATCH = "tool_dispatch"
    DATA_FABRIC = "data_fabric"
    DOWNLOAD = "download"
    VECTOR_COMPUTE = "vector_compute"
    RASTER_COMPUTE = "raster_compute"
    REMOTE_SENSING = "remote_sensing"
    STATISTICS = "statistics"
    MAP_COMPILE = "map_compile"
    RENDER = "render"
    BROWSER = "browser"
    VLM_JUDGE = "vlm_judge"
    EXPORT = "export"
    STORAGE = "storage"
    TASK_QUEUE = "task_queue"


class ResourceClass(str, Enum):
    """执行资源档（对齐 tools/registry.py ToolCost 的 light/medium/heavy 词表并
    扩展 harness 特有档；背压层据此分流 heavy/light 通道）。"""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    RASTER = "raster"
    BROWSER = "browser"
    EXPORT = "export"
    LLM = "llm"


class ExecutionPriority(int, Enum):
    """执行优先级（小者先；aging 会临时提升有效优先级）。"""

    INTERACTIVE = 0
    NORMAL = 1
    BATCH = 2


class AdmissionDecision(str, Enum):
    """准入五值（spec §8）。DEFER = 进程内排队/串行化（诚实语义，不承诺后台异步）。"""

    ACCEPT = "accept"
    ACCEPT_WITH_LIMITS = "accept_with_limits"
    DEGRADE = "degrade"
    DEFER = "defer"
    REJECT = "reject"


class RetryClass(str, Enum):
    """重试类别（RetryBudget 的记账维度；封闭词表）。"""

    TOOL = "tool"
    HTTP = "http"
    DATA_FABRIC = "data_fabric"
    WORKFLOW = "workflow"
    PI = "pi"
    SELF_HEAL = "self_heal"
    LLM = "llm"


class CancelReason(str, Enum):
    """取消源（spec §14；封闭词表）。"""

    USER_CANCEL = "user_cancel"
    SESSION_REPLACED = "session_replaced"
    PLAN_INVALIDATED = "plan_invalidated"
    TIMEOUT = "timeout"
    CLIENT_DISCONNECT = "client_disconnect"


class DegradationSemantics(str, Enum):
    """降级后的科学语义标注（spec §12 红线：绝不偷偷改变科学语义）。"""

    COMPARABLE = "comparable"          # 与完整结果可比（可进定量结论）
    APPROXIMATE = "approximate"        # 近似（需标注近似幅度）
    NON_COMPARABLE = "non_comparable"  # 不可比（只能定性/示意）


# ---------------------------------------------------------------------------
# 六类核心类型
# ---------------------------------------------------------------------------


class ResourceEstimate(BaseModel):
    """一次执行的资源估算（R1/R2 产物；evidence-backed）。"""

    schema_version: str = SCHEMA_VERSION
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH
    resource_class: ResourceClass = ResourceClass.LIGHT
    dims: Dict[Dimension, DimValue] = Field(default_factory=dict)
    raster_window: Optional[RasterWindow] = None
    cpu_class: str = "unknown"       # 封闭词表: light/heavy_cpu/...
    io_class: str = "unknown"        # light_io/network_io/local_io/heavy_io
    gpu_required: Optional[bool] = None          # None = unknown
    browser_required: Optional[bool] = None      # None = unknown
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = ""
    reason: str = ""

    def dim(self, d: Dimension) -> DimValue:
        """取维度值；缺失 = unavailable（绝不静默 0）。"""
        return self.dims.get(d) or DimValue.unavailable(f"{d.value} absent from estimate")

    def adjudged(self, d: Dimension, *,
                 conservative_floors: Optional[Dict[Dimension, float]] = None) -> float:
        return self.dim(d).adjudged(conservative_floors=conservative_floors, dim=d)

    def with_dim(self, d: Dimension, v: DimValue) -> "ResourceEstimate":
        dims = dict(self.dims)
        dims[d] = v
        return self.model_copy(update={"dims": dims})

    def overall_confidence(self) -> float:
        """有意义维度的 confidence 最小值（短板语义）；无有意义维 → 0。"""
        confs = [dv.confidence for dv in self.dims.values()
                 if dv.is_meaningful() and dv.confidence is not None]
        if self.confidence is not None:
            confs.append(self.confidence)
        return min(confs) if confs else 0.0

    def as_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "subsystem": self.subsystem.value,
            "resource_class": self.resource_class.value,
            "dims": {d.value: dv.model_dump(exclude_none=True) for d, dv in self.dims.items()},
            "raster_window": self.raster_window.model_dump() if self.raster_window else None,
            "cpu_class": self.cpu_class,
            "io_class": self.io_class,
            "gpu_required": self.gpu_required,
            "browser_required": self.browser_required,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
        }


class ResourceDemand(BaseModel):
    """一次执行对 governor 的资源请求（身份 + 估算 + 执行策略输入）。"""

    schema_version: str = SCHEMA_VERSION
    session_id: str = ""
    goal_id: str = ""
    turn_id: str = ""
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH
    tool_name: str = ""
    estimate: ResourceEstimate = Field(default_factory=ResourceEstimate)
    priority: ExecutionPriority = ExecutionPriority.NORMAL
    #: 请求方愿意等待排队的上界（秒）；None = 用 subsystem 默认 max_wait。
    max_wait_s: Optional[float] = None
    deadline_s: Optional[float] = None    # 层级 deadline 传播锚点（G9）
    retry_class: Optional[RetryClass] = None
    attempt: int = 1                      # 1 = 首次；>1 = 重试（受 RetryBudget 管）
    created_at: float = Field(default_factory=time.monotonic)

    def scope_key(self) -> str:
        """会话作用域键（session 维预算/背压用；无 session → 全局兜底）。"""
        return self.session_id or "session:anonymous"


class ResourceBudget(BaseModel):
    """一个作用域上的预算上限（R4；max 语义，不承诺分配）。

    维度 → 上限；缺维 = 该维不限（诚实：预算表是封闭词表的显式声明，
    不隐式继承）。provisional 值来自校准 manifest（config/governor_budgets.json）。
    """

    schema_version: str = SCHEMA_VERSION
    scope: str = "session"        # turn / goal / session / subsystem / global
    scope_id: str = "anonymous"
    limits: Dict[Dimension, float] = Field(default_factory=dict)
    max_heavy_concurrent: Optional[int] = None
    max_browser_renders: Optional[int] = None
    max_exports: Optional[int] = None
    max_external_calls: Optional[int] = None
    max_retry_tokens: Optional[int] = None
    #: provisional=True：来自校准首轮，只记录/告警不硬拒（对齐 ADR-0159
    #: ratchet 的 provisional 纪律：先观测后拦截）。
    provisional: bool = True
    source: str = "default"

    def limit_for(self, d: Dimension) -> Optional[float]:
        return self.limits.get(d)


class ResourceReservation(BaseModel):
    """准入成功后的资源占位（release 前有效；线程安全由 governor 保证）。"""

    schema_version: str = SCHEMA_VERSION
    reservation_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: str = ""
    goal_id: str = ""
    turn_id: str = ""
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH
    resource_class: ResourceClass = ResourceClass.LIGHT
    #: 实际记账量（adjudged 后的确定值；release 按此原样归还）
    charged: Dict[Dimension, float] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.monotonic)
    released: bool = False
    cancelled: bool = False

    def age_s(self) -> float:
        return max(0.0, time.monotonic() - self.created_at)


class ResourceUsage(BaseModel):
    """执行后的实际观测（R16 estimate-vs-actual 的 actual 半边）。"""

    schema_version: str = SCHEMA_VERSION
    session_id: str = ""
    tool_name: str = ""
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH
    dims: Dict[Dimension, float] = Field(default_factory=dict)
    wall_time_s: Optional[float] = None
    status: str = "completed"    # completed / failed / cancelled / degraded
    degraded: bool = False
    degraded_reason: str = ""
    semantics: Optional[DegradationSemantics] = None
    retries: int = 0
    fallbacks: int = 0
    recorded_at: float = Field(default_factory=time.monotonic)


class ResourceDecision(BaseModel):
    """准入裁决（R3 产物；解释性 reason codes 全量留痕）。"""

    schema_version: str = SCHEMA_VERSION
    decision: AdmissionDecision
    reasons: List[str] = Field(default_factory=list)
    #: accept_with_limits 时施加的执行限幅（如降采样后的 pixel/feature 上限）
    limits: Dict[Dimension, float] = Field(default_factory=dict)
    #: defer 时的排队 hint（scope 名，admission 不自带队列 —— backpressure 层消费）
    queue_hint: Optional[str] = None
    #: degrade 时附带的降级建议（执行权在调用方，D7 纪律）
    degrade_hint: Optional[Dict] = None
    #: reject 时的可行动建议（BudgetExceededError 同款纪律）
    suggestions: List[str] = Field(default_factory=list)
    estimate_snapshot: Optional[Dict] = None
    mode: str = "enforce"        # enforce / observe（observe 时 decision 仅留痕）
    decided_at: float = Field(default_factory=time.monotonic)

    @property
    def allowed(self) -> bool:
        """enforce 模式下是否放行（observe 模式恒 True，由 governor 保证）。"""
        return self.decision in (
            AdmissionDecision.ACCEPT,
            AdmissionDecision.ACCEPT_WITH_LIMITS,
            AdmissionDecision.DEFER,       # defer 仍在本生命周期内执行（排队后）
        )

    def as_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "limits": {d.value: v for d, v in self.limits.items()},
            "queue_hint": self.queue_hint,
            "degrade_hint": dict(self.degrade_hint) if self.degrade_hint else None,
            "suggestions": list(self.suggestions),
            "mode": self.mode,
        }


def monotonic_seq(prefix: str) -> str:
    """单调可读 id（日志关联用；非强唯一性保证）。"""
    return f"{prefix}-{next(_SEQ):08d}"


_SEQ = itertools.count(1)


def decision_id() -> str:
    return monotonic_seq("gd")


__all__ = [
    "SCHEMA_VERSION",
    "Certainty",
    "Dimension",
    "CONSERVATIVE_FLOORS",
    "DimValue",
    "RasterWindow",
    "Subsystem",
    "ResourceClass",
    "ExecutionPriority",
    "AdmissionDecision",
    "RetryClass",
    "CancelReason",
    "DegradationSemantics",
    "ResourceEstimate",
    "ResourceDemand",
    "ResourceBudget",
    "ResourceReservation",
    "ResourceUsage",
    "ResourceDecision",
    "decision_id",
]
