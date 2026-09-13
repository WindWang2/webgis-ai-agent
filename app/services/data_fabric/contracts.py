"""ads-v1 acquisition-supply contracts D1–D4 (ADR-0170).

Four frozen contracts for the data-supply line (adaptive-data-supply/v1):

- **D1** ``D1DatasetDescriptor`` — the single data interface towards the
  cartography line (V11). Extends the existing
  ``app.schemas.data_fabric_schema.DatasetDescriptor`` (ADR-0094 honest
  defaults) with supply-side fields; all additions are optional so every
  existing producer/consumer keeps working unchanged.
- **D2** ``AcquisitionPlan`` — a serializable, diffable, replayable plan:
  ordered steps + cost estimate + budget + human-readable explain.
- **D3** ``FallbackDecision`` — per-degradation record. Same shape as the
  cartography-side ADR-0151 chain but an independent namespace (``ds.*``);
  ``comparable=False`` forces downstream artifacts to be marked non-comparable.
- **D4** ``AcquisitionFact`` — one acquisition observation (rows/bytes/
  latency/retries/degraded/drift/wave) feeding observability and the DS8 ratchet.

Freeze rule (task book §2): after DS0 these shapes are frozen — later waves
may only add optional fields together with an upgrade test; renaming or
re-typing requires an ADR and updates to every consumer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.data_fabric_schema import DatasetDescriptor

# Contract version. Bump minor for additive changes (with upgrade test),
# major only via ADR with all consumers migrated.
CONTRACTS_VERSION = "1.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── D1 · DatasetDescriptor (supply-side extension) ──────────────────────────


class TemporalCoverage(BaseModel):
    """时间覆盖（ISO8601 字符串；None = 未知，诚实默认，不伪造）。"""

    model_config = ConfigDict(extra="allow")

    start: Optional[str] = None
    end: Optional[str] = None
    granularity: Optional[str] = None  # e.g. yearly / monthly / daily / instant
    declared_only: bool = True  # True = 来自源声明，未经本地实测


class FreshnessInfo(BaseModel):
    """新鲜度：源声明/实测的更新情况。"""

    model_config = ConfigDict(extra="allow")

    updated_at: Optional[str] = None
    update_frequency: Optional[str] = None  # e.g. daily / monthly / irregular / unknown
    staleness_days: Optional[int] = None  # 实测过期天数；None = 未知


class QualitySignals(BaseModel):
    """A12 取数前能力握手（DS0.4）：只声明、不修复。

    修复仍归 V11 W3 ``spatial_repair_pipeline``（制图前置质量门禁 ADR-0153）；
    本结构只把源**自述**的质量信号带给下游，供计划与降级决策消费。
    """

    model_config = ConfigDict(extra="allow")

    declared_crs: Optional[str] = None  # 源声明 CRS（未验证时配合 declared_only）
    declared_completeness: Optional[float] = None  # 0..1，源自述完整度
    known_issues: List[str] = Field(default_factory=list)  # e.g. ["gcj02_offset", "missing_2019"]
    verified: bool = False  # 源/数据集是否经过真实性验证（未验证降权，任务书 §0.5）


class CostHint(BaseModel):
    """源侧给出的代价提示（DS3 代价模型的先验；非实测值）。"""

    model_config = ConfigDict(extra="allow")

    rows: Optional[int] = None
    bytes: Optional[int] = None
    latency_ms: Optional[float] = None
    quota: Optional[float] = None
    local: bool = False  # 本地资产（local_first / LOCAL_GEODATA_DIR）≈ 零网络代价


class D1DatasetDescriptor(DatasetDescriptor):
    """D1：数据集统一描述（ads-v1 供给面扩展，全部向后兼容可选字段）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    contract_version: str = CONTRACTS_VERSION

    # 版本语义（DS5 消费）：latest = 跟随源最新；pinned = 锁定到某修订。
    version: str = "latest"
    version_pinned: bool = False

    temporal_coverage: Optional[TemporalCoverage] = None
    granularity: Optional[str] = None  # 省/市/县/乡镇/街道/网格/流域 …（DS6 对接）
    license: Optional[str] = None
    freshness: Optional[FreshnessInfo] = None
    quality_signals: QualitySignals = Field(default_factory=QualitySignals)
    cost_hint: Optional[CostHint] = None

    @classmethod
    def from_fabric_descriptor(
        cls, d: DatasetDescriptor, **overrides: Any
    ) -> "D1DatasetDescriptor":
        """从既有 fabric DatasetDescriptor 构造（零拷贝升级缝）。"""
        payload = d.model_dump(exclude_none=False)
        payload.update(overrides)
        return cls(**payload)


# ── D2 · AcquisitionPlan ─────────────────────────────────────────────────────


class AcquisitionStep(BaseModel):
    """单个取数步骤。step_type 限定词表；params 步骤自有参数。"""

    model_config = ConfigDict(extra="allow")

    step_type: Literal[
        "source_select",
        "bbox_clip",
        "field_projection",
        "aggregate_pushdown",
        "time_filter",
        "pagination",
        "sampling",
        "version_pin",
    ]
    params: Dict[str, Any] = Field(default_factory=dict)
    source_id: Optional[str] = None  # 该步落在哪个源上（None=承前）


class CostEstimate(BaseModel):
    """计划级代价估算（DS3 代价模型产出；None=该项不可估）。"""

    model_config = ConfigDict(extra="allow")

    rows: Optional[int] = None
    bytes: Optional[int] = None
    latency_ms: Optional[float] = None
    quota: Optional[float] = None


class AcquisitionBudget(BaseModel):
    """预算约束（DS3：超预算给降级建议而非直接失败）。"""

    model_config = ConfigDict(extra="allow")

    max_rows: Optional[int] = None
    max_bytes: Optional[int] = None
    max_ms: Optional[float] = None
    max_quota: Optional[float] = None


class AcquisitionPlan(BaseModel):
    """D2：取数计划。可序列化、可 diff、可重放（同 plan + 同版本 pin → 同结果）。"""

    model_config = ConfigDict(extra="allow")

    contract_version: str = CONTRACTS_VERSION
    plan_id: str
    dataset_key: str
    version: str = "latest"  # 与 D1.version 同语义；重放要求 pinned
    steps: List[AcquisitionStep] = Field(default_factory=list)
    cost_estimate: CostEstimate = Field(default_factory=CostEstimate)
    budget: Optional[AcquisitionBudget] = None
    explain: str = ""  # 人类可读计划说明（走了哪步、推下去什么、预计多少行）
    created_at: Optional[str] = None

    def diff(self, other: "AcquisitionPlan") -> Dict[str, Any]:
        """结构化 diff（重放校验 / 计划评审用）：步骤序列与关键字段差异。"""
        diffs: Dict[str, Any] = {}
        if self.dataset_key != other.dataset_key:
            diffs["dataset_key"] = (self.dataset_key, other.dataset_key)
        if self.version != other.version:
            diffs["version"] = (self.version, other.version)
        self_steps = [s.model_dump() for s in self.steps]
        other_steps = [s.model_dump() for s in other.steps]
        if self_steps != other_steps:
            diffs["steps"] = (self_steps, other_steps)
        if self.cost_estimate.model_dump() != other.cost_estimate.model_dump():
            diffs["cost_estimate"] = (
                self.cost_estimate.model_dump(),
                other.cost_estimate.model_dump(),
            )
        if self.budget and other.budget:
            if self.budget.model_dump() != other.budget.model_dump():
                diffs["budget"] = (self.budget.model_dump(), other.budget.model_dump())
        return diffs


# ── D3 · FallbackDecision（数据面，独立命名空间） ────────────────────────────


class FallbackDecision(BaseModel):
    """D3：一次降级/换源的决策记录（与 ADR-0151 制图面同构、命名空间独立）。

    ``comparable=False`` 时下游**必须**在产物标注「结果不可比」（任务书 §2/§3-DS4
    硬约束）；静默换源是被禁止的。
    """

    model_config = ConfigDict(extra="allow")

    trigger: Literal[
        "timeout", "5xx", "429", "quota", "empty_result", "truncated",
        "schema_mismatch", "circuit_open", "probe_failed", "other",
    ]
    from_source: str
    to_source: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    comparable: bool = False  # 备用源与原源粒度/覆盖不同 → False（保守默认）
    degradation_notes: Optional[str] = None
    decided_at: Optional[str] = None


# ── D4 · AcquisitionFact ─────────────────────────────────────────────────────


class AcquisitionFact(BaseModel):
    """D4：一次取数的事实观测（DS8 埋点全字段落库 + ratchet 驱动）。"""

    model_config = ConfigDict(extra="allow")

    contract_version: str = CONTRACTS_VERSION
    request_id: str
    dataset_key: str
    source_id: Optional[str] = None
    version: str = "latest"
    rows: int = 0
    bytes: int = 0
    latency_ms: float = 0.0
    retries: int = 0
    degraded: bool = False
    outcome: Literal["success", "degraded", "failed"] = "success"
    fallback: Optional[FallbackDecision] = None
    drift: Optional[str] = None  # ChangeClass（versioning.compare_revisions 输出）
    wave: str = ""  # 波次/里程碑标签（M1…M5），ratchet 按波聚合
    ts: Optional[str] = None


__all__ = [
    "CONTRACTS_VERSION",
    "TemporalCoverage",
    "FreshnessInfo",
    "QualitySignals",
    "CostHint",
    "D1DatasetDescriptor",
    "AcquisitionStep",
    "CostEstimate",
    "AcquisitionBudget",
    "AcquisitionPlan",
    "FallbackDecision",
    "AcquisitionFact",
]
