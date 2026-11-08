"""G10 项目数据刷新 · 领域契约（纯类型，零 I/O）。

设计红线：
- 逻辑身份（logical_id）与内容版本（version_token / fingerprints）分离；
  同名文件不自动相同，不同 ref 不自动意味着变化。
- 证据级别显式：只有 CONTENT_EXACT + 完整四维指纹 + 未抽样/未截断
  才允许声称「精确数据变化」；ETag/更新时间只到 REVISION_TOKEN /
  TIMESTAMP_ONLY，不能升级成内容结论。
- 所有集合有界；大 FeatureCollection 不进模型上下文。
- 本模块不新建第二执行器 / registry / MapSpec / session truth store，
  只是刷新事务的领域数据契约。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REFRESH_SCHEMA_VERSION = 1
PROPOSAL_FINGERPRINT_PREFIX = "g10:"
MAX_SOURCES = 64
MAX_DIFF_IDS = 256
#: 生产快照允许逐要素对齐的上限；超过即截断并强制标 truncated/sampled，
#: 绝不静默丢弃后仍声称 EXACT 精确计数（独立审查 C · P1-1）。
MAX_SNAPSHOT_FEATURES = 20_000
MAX_WARNINGS = 16
MAX_TEXT = 512


class SourceState(str, Enum):
    """来源可用性词表（与 project_runtime.promotion 既有分类映射）。"""

    PINNED = "pinned"                  # 冻结/交付固定版本，不随源更新漂移
    REFRESHABLE = "refreshable"        # 可检查更新
    EPHEMERAL = "ephemeral"            # 临时会话 ref，刷新后可能不可复现
    MISSING = "missing"                # 源已失权/删除
    VERSION_UNKNOWN = "version_unknown"  # 无可验证内容版本


class EvidenceLevel(str, Enum):
    """版本证据级别（决定能否声称精确变化）。"""

    CONTENT_EXACT = "content_exact"      # 可验证内容指纹 / descriptor fingerprint
    REVISION_TOKEN = "revision_token"    # content_revision / ETag / commit token
    TIMESTAMP_ONLY = "timestamp_only"    # 仅更新时间或 mtime
    NONE = "none"                        # 无证据


class ChangeEvidence(str, Enum):
    """差异判定证据四态（UPD-02）。"""

    EXACT = "exact"                  # 可逐要素对齐的精确判定
    CONSERVATIVE = "conservative"    # 只能整体判定变化，需保守重算
    UNKNOWN = "unknown"              # 证据缺失，不可判定（≠ 没变）
    UNVERIFIED = "unverified"        # 未执行验证（远程/预算/权限）


class FeatureIdKind(str, Enum):
    STABLE = "stable"          # 稳定 feature ID
    NONE = "none"              # 无 ID
    POSITIONAL = "positional"  # 只有行位置 —— 禁止当身份


class PlanDecision(str, Enum):
    UNCHANGED = "unchanged"
    REUSE = "reuse"
    RECOMPUTE = "recompute"
    REBIND = "rebind"
    CONFLICT = "conflict"
    UNVERIFIABLE = "unverifiable"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DISCARDED = "discarded"
    ADOPTED = "adopted"


class ProposalStatus(str, Enum):
    PREVIEW = "preview"            # 只读预览，未执行
    EXECUTING = "executing"
    READY = "ready"                # 候选完成，待采纳
    ADOPTED = "adopted"
    DISCARDED = "discarded"
    DRIFTED = "drifted"            # 版本/权限/revision 漂移，需重预览
    FAILED = "failed"


class FeatureCounts(BaseModel):
    """精确要素差异计数（只有 EXACT 证据才允许非 None）。"""

    added: int = 0
    removed: int = 0
    attribute_changed: int = 0
    geometry_changed: int = 0


class DiffBudget(BaseModel):
    """差异检测预算；超预算必须保守重算，不得 silently unchanged。"""

    max_features: int = Field(default=50_000, ge=1, le=1_000_000)
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)


class SourceSnapshot(BaseModel):
    """某来源在某一时刻的可比较快照（领域数据，非 UI mock）。

    生产路径由 ``app/services/project_runtime/refresh/sources.py`` 从
    ``ProjectDataset`` / runtime snapshot / ref descriptor / lakehouse
    manifest 填充；测试用本地合成 fixture 走**同一套判定代码**。
    """

    # R2-P2：对已构造的 BaseModel 实例再次 model_validate 时也要重新截断，
    # 否则可绕过 truncated/sampled 置位。
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    logical_id: str = Field(min_length=1, max_length=160)
    display_name: str = ""
    kind: str = "vector"                # vector | table | raster | source
    state: SourceState = SourceState.REFRESHABLE
    evidence: EvidenceLevel = EvidenceLevel.NONE
    version_token: str = ""             # 内容版本 token（可空）
    fingerprints: Dict[str, Optional[str]] = Field(default_factory=dict)

    schema_fields: Dict[str, str] = Field(default_factory=dict)  # field -> "type|unit"
    crs: str = ""
    bbox: Optional[List[float]] = None
    temporal_window: Optional[List[str]] = None
    denominator: str = ""
    method_version: str = ""

    features: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    feature_id_kind: FeatureIdKind = FeatureIdKind.STABLE

    raster_tiles: Dict[str, str] = Field(default_factory=dict)
    raster_dependencies: List[str] = Field(default_factory=list)

    #: 该来源在分析图中对应的 data role 节点；为空时按 logical_id 确定性推导。
    analysis_roles: List[str] = Field(default_factory=list)
    #: 分母变化影响的节点（默认归一化统计族；由生产读侧显式覆盖）。
    denominator_nodes: List[str] = Field(default_factory=lambda: ["analysis:normalized"])

    owner_id: str = ""
    sampled: bool = False
    truncated: bool = False
    available_bytes: int = 0

    @field_validator("logical_id", "display_name", "version_token", "crs", "denominator", "method_version", "owner_id")
    @classmethod
    def _bounded_text(cls, v: str) -> str:
        return str(v)[:MAX_TEXT]

    @field_validator("bbox")
    @classmethod
    def _bounded_bbox(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if len(v) != 4 or any(not isinstance(x, (int, float)) for x in v):
            raise ValueError("bbox must be 4 finite numbers")
        import math
        if any(not math.isfinite(float(x)) for x in v):
            raise ValueError("bbox must be finite")
        return [float(x) for x in v]

    @model_validator(mode="before")
    @classmethod
    def _bound_maps_and_flag_truncation(cls, value: Any) -> Any:
        """有界截断必须**同时**置 truncated/sampled，绝不静默丢弃要素。

        否则超界数据会以「精确证据」算出全 0 的差异计数，进而得到
        recompute=[] / conservative=False 的 silently unchanged（R1-P1-1）。
        """
        if isinstance(value, BaseModel):
            value = value.model_dump()
        if not isinstance(value, dict):
            return value
        v = dict(value)
        truncated = False
        for key, limit in (("schema_fields", MAX_DIFF_IDS),
                           ("features", MAX_SNAPSHOT_FEATURES),
                           ("raster_tiles", MAX_DIFF_IDS),
                           ("fingerprints", MAX_DIFF_IDS)):
            raw = v.get(key)
            if isinstance(raw, dict) and len(raw) > limit:
                v[key] = dict(list(raw.items())[:limit])
                truncated = True
        if truncated:
            v["truncated"] = True
            v["sampled"] = True
        return v


class SourceDiff(BaseModel):
    """UPD-02 差异报告：精确 / 保守 / 未知 / 未验证分别披露。"""

    model_config = ConfigDict(extra="forbid")

    logical_id: str
    change_class: str = "unknown"      # ChangeClass.value（fingerprints 单一事实源）
    evidence: ChangeEvidence = ChangeEvidence.UNKNOWN
    alignment: str = "unavailable"     # stable_id | unavailable | positional_rejected
    exact_counts: Optional[FeatureCounts] = None

    fields_added: List[str] = Field(default_factory=list)
    fields_removed: List[str] = Field(default_factory=list)
    fields_type_changed: List[str] = Field(default_factory=list)
    fields_unit_changed: List[str] = Field(default_factory=list)

    features_added: List[str] = Field(default_factory=list)
    features_removed: List[str] = Field(default_factory=list)
    features_attribute_changed: List[str] = Field(default_factory=list)
    features_geometry_changed: List[str] = Field(default_factory=list)

    crs_changed: bool = False
    bbox_changed: bool = False
    temporal_changed: bool = False
    denominator_changed: bool = False
    method_version_changed: bool = False

    raster_tiles_changed: List[str] = Field(default_factory=list)
    expanded_dependencies: List[str] = Field(default_factory=list)

    #: 由 diff 推导的分析图种子目标（节点 id 或 data role）
    seed_targets: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    truncated: bool = False
    sampled: bool = False
    reason: str = ""


class SourceInventoryItem(BaseModel):
    """UPD-01 来源清单条目（有界，可直接投影给 UI/Pi）。"""

    model_config = ConfigDict(extra="forbid")

    logical_id: str
    display_name: str = ""
    kind: str = "vector"
    state: SourceState = SourceState.VERSION_UNKNOWN
    evidence: EvidenceLevel = EvidenceLevel.NONE
    baseline_version: str = ""
    current_version: str = ""
    owner_id: str = ""
    crs: str = ""
    bbox: Optional[List[float]] = None
    temporal_window: Optional[List[str]] = None
    verdict: str = "unknown"           # unchanged | changed | missing | unverifiable
    note: str = ""


class SourceInventory(BaseModel):
    items: List[SourceInventoryItem] = Field(default_factory=list)
    truncated: bool = False
    warnings: List[str] = Field(default_factory=list)
    fingerprint: str = ""


class RefreshPlan(BaseModel):
    """UPD-03 只读更新计划（不新建第二 DAG，复用 compute_affected_subgraph）。"""

    model_config = ConfigDict(extra="forbid")

    recompute: List[str] = Field(default_factory=list)
    reuse: List[str] = Field(default_factory=list)
    decisions: Dict[str, str] = Field(default_factory=dict)
    changed_dimensions: List[str] = Field(default_factory=list)
    explanations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    conservative: bool = False
    style_only: bool = False
    style_only_reason: str = ""
    preserved_user_edits: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    estimated_budget: Dict[str, Any] = Field(default_factory=dict)
    reuse_fingerprint: str = ""
    proposal_fingerprint: str = ""


class MergeConflict(BaseModel):
    kind: str            # deleted_annotation_target | removed_field | hidden_invalid_layer | lock_violation
    entity_id: str
    detail: str = ""


class MergeResult(BaseModel):
    merged_mapspec: Dict[str, Any] = Field(default_factory=dict)
    preserved: List[str] = Field(default_factory=list)
    conflicts: List[MergeConflict] = Field(default_factory=list)
    hard_semantic_errors: List[str] = Field(default_factory=list)
    user_wins: bool = True
    adoptable: bool = True


__all__ = [
    "REFRESH_SCHEMA_VERSION", "PROPOSAL_FINGERPRINT_PREFIX", "MAX_SOURCES",
    "SourceState", "EvidenceLevel", "ChangeEvidence", "FeatureIdKind",
    "PlanDecision", "CandidateStatus", "ProposalStatus",
    "FeatureCounts", "DiffBudget", "SourceSnapshot", "SourceDiff",
    "SourceInventoryItem", "SourceInventory", "RefreshPlan",
    "MergeConflict", "MergeResult",
]
