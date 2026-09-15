"""agent_swarm 统一输出契约（ADR-0188 D3/D4）。

- **Data Scout** 产出 ADS 既有 D1 ``D1DatasetDescriptor``（不在此重复定义）；
- **GeoCompute** 产出 ``SpatialProfileRef`` —— 结果的唯一通货，携带
  ref_id 提货券与有界摘要，序列化字节硬上限
  ``SERIALIZATION_BUDGET_BYTES``（Zero Big Data in Context）。

冻结规则与 data_fabric.contracts 同门：v1 之后只允许追加可选字段并
携带升级测试；改名/改类型必须过 ADR。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.services.data_fabric.contracts import (
    AcquisitionFact,
    D1DatasetDescriptor,
    FallbackDecision,
)

#: SpatialProfileRef 序列化字节硬上限（8 KB）。超限处理顺序：
#: metadata 有界截断（truncated=True 诚实标注）→ 仍超限 → typed 失败。
SERIALIZATION_BUDGET_BYTES = 8 * 1024

#: 契约版本（追加可选字段时 bump minor + 升级测试）。
CONTRACTS_VERSION = "1.0"


class SpatialProfileTooLargeError(ValueError):
    """SpatialProfileRef 无法收敛到 8KB 预算内（无 metadata 可截）。

    诚实失败：绝不静默裁剪载荷、绝不把原始几何放回上下文。
    """


class VolumeEstimate(BaseModel):
    """体积估算结果（诚实：估算不到的维度留 None，method 披露来源）。"""

    rows: Optional[int] = None
    bytes: Optional[int] = None
    area_km2: Optional[float] = None
    method: str = "unknown"  # cost_hint | rows_hint | bbox_density | unknown


class SpatialProfileRef(BaseModel):
    """GeoCompute 结果提货券（Zero Big Data in Context 的唯一通货）。

    ``ref_id`` 是确定性取货位（``gc-{plan_id}-{尾节点语义指纹}``）；
    结果本体存 session ref / durable job 行，经 get_execution_run /
    job 轮询解析 —— 上下文里只允许本契约形态的有界摘要。
    """

    version: str = CONTRACTS_VERSION
    ref_id: Optional[str] = None
    plan_id: str
    plan_digest: str = ""
    status: Literal["submitted", "running", "completed", "failed", "degraded"] = (
        "submitted"
    )
    rows: Optional[int] = None
    crs: Optional[str] = None
    crs_defense: Optional[str] = None  # "auto_utm" | None
    volume_estimate: Optional[VolumeEstimate] = None
    job_id: Optional[int] = None
    celery_task_id: Optional[str] = None
    duration_ms: Optional[float] = None
    summary: str = ""  # 写入时截断到 _MAX_SUMMARY_LEN
    notes: Tuple[str, ...] = Field(default_factory=tuple)  # ≤8 条、各 ≤_MAX_NOTE_LEN
    truncated: bool = False  # metadata 被预算截断的诚实标注
    metadata: Dict[str, Any] = Field(default_factory=dict)  # 截断牺牲品

    _MAX_SUMMARY_LEN: ClassVar[int] = 600
    _MAX_NOTE_LEN: ClassVar[int] = 200
    _MAX_NOTES: ClassVar[int] = 8

    def to_json_bytes(self) -> bytes:
        """UTF-8 JSON 序列化字节（8KB 闸的度量口径）。"""
        return self.model_dump_json().encode("utf-8")


class DataScoutReport(BaseModel):
    """Data Scout 的统一报告：D1 descriptor + D3 降级链 + D4 观测。"""

    ok: bool
    descriptor: Optional[D1DatasetDescriptor] = None
    source_used: Optional[str] = None
    decisions: List[FallbackDecision] = Field(default_factory=list)
    fact: Optional[AcquisitionFact] = None
    error: Optional[str] = None  # 诚实失败原因（ok=False 时必有）
    aligned_fields: Dict[str, str] = Field(default_factory=dict)
    crs_alignment: Optional[str] = None


class ComputeRequest(BaseModel):
    """GeoCompute 编排请求（Agent Plane → Data Plane 的最小意图声明）。"""

    model_config = {"extra": "forbid"}

    operation: str
    dataset_ref: str  # session ref 或 dataset key（input handoff 通货）
    operation_params: Dict[str, Any] = Field(default_factory=dict)
    bbox: Optional[List[float]] = None
    rows_hint: Optional[int] = None
    session_id: Optional[str] = None
    deadline_s: Optional[float] = None
    crs: Optional[str] = None
    descriptor: Optional[D1DatasetDescriptor] = None  # 上游 D1（cost_hint 估算用）


class ComputeSubmission(BaseModel):
    """一次 durable 计算提交的回执（编排器绝不同步执行重算子）。"""

    model_config = {"extra": "forbid"}

    plan_id: str
    node_count: int
    job_id: Optional[int] = None
    celery_task_id: Optional[str] = None
    ref: SpatialProfileRef
    #: 构造期已 validate_plan 的执行图对象（进程内消费；不序列化）。
    plan_built: Any = None
    #: 执行图 JSON（build_plan_from_json 可重建 + 复验，往返合法）。
    plan_json: Dict[str, Any] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────
# ADR-0189 D3：Cartographer / CriticAuditor 输出契约（追加式冻结）
# ────────────────────────────────────────────────────────────────

#: MapSpec 载荷在上下文中的摘要长度上限（同 SpatialProfileRef 口径）。
_MAX_DELIVERY_SUMMARY_LEN = 600
_MAX_DELIVERY_WARNING_LEN = 200


class MapSpecDeliveryRef(BaseModel):
    """Cartographer 交付券（ADR-0189 D3）：MapSpec 载荷不过境。

    ``ref_id`` 指向进程内 ``ArtifactLedger`` 的取货位；券本体只携带
    fingerprint（代际锁定）+ 有界摘要（层数 / 图例态 / 分类裁决 /
    组件词表）。载荷走私防线与 SpatialProfileRef 同门。
    """

    model_config = {"extra": "forbid"}

    version: str = CONTRACTS_VERSION
    ref_id: Optional[str] = None  # "ref:mapspec-*"（ledger 缺席时为 None）
    capability: Literal["thematic_map"] = "thematic_map"
    mapspec_fingerprint: str = ""  # cartographic_fingerprint 同源
    digest: str = ""  # 载荷 sha256 前 12 位
    revision: int = 0  # 对抗回路 revise 递增
    layer_count: int = 0
    legend_visible: Optional[bool] = None
    classification: Dict[str, Any] = Field(default_factory=dict)
    #: {field, method, k, palette, rejected_methods}
    components: List[str] = Field(default_factory=list)  # 组件类型词表 ≤12
    warnings: List[str] = Field(default_factory=list)  # ≤8 条、各 ≤200
    summary: str = ""  # ≤600
    truncated: bool = False

    _MAX_WARNINGS: ClassVar[int] = 8

    def to_json_bytes(self) -> bytes:
        """UTF-8 JSON 序列化字节（8KB 闸的度量口径）。"""
        return self.model_dump_json().encode("utf-8")


class DeliveryAuditReport(BaseModel):
    """CriticAuditor《交付质量审计单》（ADR-0189 D2）。

    ``goal_score`` 是**派生口径**（counts.fulfilled / len(required_ids)），
    不是第二 verdict —— ADR-0183 红线；非 None 时
    ``goal_score_derivation`` 必填披露。``verdict`` 只有三值；
    ``vetoes`` 是不可抵赖因果证据链（veto id + 规则 + 指纹 + 建议）。
    """

    model_config = {"extra": "forbid"}

    version: str = CONTRACTS_VERSION
    audited_ref_id: str = ""
    audited_fingerprint: str = ""
    round_index: int = 0
    verdict: Literal["pass", "fail", "not_evaluated"] = "not_evaluated"
    goal_score: Optional[float] = None
    goal_score_derivation: str = ""
    success_threshold: Optional[float] = None
    uncovered_requirements: List[str] = Field(default_factory=list)  # ≤12
    cartography_risks: List[str] = Field(default_factory=list)  # 规则码 ≤12
    vetoes: List[Dict[str, Any]] = Field(default_factory=list)  # ≤8
    improvement_notes: List[str] = Field(default_factory=list)  # ≤8 × ≤240
    review_status: str = ""  # quality_loop 词表投影

    _MAX_UNCOVERED: ClassVar[int] = 12
    _MAX_RISKS: ClassVar[int] = 12
    _MAX_VETOES: ClassVar[int] = 8
    _MAX_NOTES: ClassVar[int] = 8
    _MAX_NOTE_LEN: ClassVar[int] = 240

    def to_json_bytes(self) -> bytes:
        """UTF-8 JSON 序列化字节（8KB 闸的度量口径）。"""
        return self.model_dump_json().encode("utf-8")


__all__ = [
    "CONTRACTS_VERSION",
    "SERIALIZATION_BUDGET_BYTES",
    "SpatialProfileTooLargeError",
    "VolumeEstimate",
    "SpatialProfileRef",
    "DataScoutReport",
    "ComputeRequest",
    "ComputeSubmission",
    "MapSpecDeliveryRef",
    "DeliveryAuditReport",
]
