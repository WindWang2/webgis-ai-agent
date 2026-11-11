"""Data Delivery Schema — ref/revision 驱动的多格式数据成果交付（G18）。

有版本的交付规格（DeliverySpec）、预览（DeliveryPreview）、状态
（DeliveryJobView）与回执（DeliveryReceipt）的 Pydantic 契约。

设计纪律（任务书不变量）：
- **规格有界**：不接受无边界任意 JSON——源锚是有限枚举 + 有界引用；
  scope 只允许 full / 有界稳定 ID 列表 / typed predicate+bbox；
  字段投影与重命名有上限。大 payload 永远经 ref 表达，不进工具参数。
- **预览先于执行**：损失、阻断项与需确认项都在预览中可见；提交时服务端
  复检并强制损失确认（acknowledged_losses 覆盖全部 required codes）。
- **回执诚实**：实际行数/字节/哈希、真实发生的变换与降级、源身份与
  范围证据；估算值一律带 estimate 标注，绝不冒充实测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 交付格式词汇表（核心 + 受限）。核心：geojson/gpkg/geoparquet/csv；
#: 受限：shapefile_zip/flatgeobuf（能力以运行时驱动探测为准）。
DeliveryFormat = Literal[
    "geojson", "gpkg", "geoparquet", "csv", "shapefile_zip", "flatgeobuf"
]

#: 源锚类型词汇表。
SourceKind = Literal["session_ref", "fabric_parquet", "project_artifact", "table_rows"]

#: 范围类型词汇表。
ScopeType = Literal["full", "ids", "filter"]

#: 混合几何策略：单图层（Unknown/混合）或按几何类型分组。
GeometryGrouping = Literal["single", "by_type"]


class DeliverySourceRef(BaseModel):
    """交付源锚 —— 指向一个已登记的数据引用或项目产物修订。

    恰好一种 kind 生效；对应字段必须在字符集白名单内（路由/服务层
    复检，绝不把 ref 文本拼接成未经校验的本地路径）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    session_id: Optional[str] = Field(default=None, max_length=128)
    """session_ref / fabric_parquet / table_rows 的会话归属。"""
    ref: Optional[str] = Field(default=None, max_length=256)
    """session ref id（如 ref:data-fabric-xxxx）或 ref:fabric-parquet/<id>。"""
    project_id: Optional[str] = Field(default=None, max_length=128)
    """project_artifact 的项目归属。"""
    artifact_id: Optional[str] = Field(default=None, max_length=128)
    revision_no: Optional[int] = Field(default=None, ge=1)
    """缺省 head；显式修订号 = 冻结到该不可变修订（pinned 语义）。"""
    table_ref: Optional[str] = Field(default=None, max_length=256)
    """table_rows：session 内 rows 型 payload 的 ref（能力上界由 store 预算约束）。"""


class DeliveryFieldSelection(BaseModel):
    """字段投影与重命名（有界；Shapefile 长字段名映射的显式载体）。"""

    model_config = ConfigDict(extra="forbid")

    fields: Optional[List[str]] = Field(default=None, max_length=256)
    """字段投影（保持给出顺序）；None = 全字段（快照顺序）。"""
    field_map: Optional[Dict[str, str]] = Field(
        default=None, description="输出字段名映射（如 Shapefile ≤10 字符约束的显式解）"
    )
    include_geometry: bool = True
    """CSV：是否附带几何（WKT 列）；矢量格式恒含几何。"""


class DeliveryScope(BaseModel):
    """交付范围 —— full / 稳定 ID 列表 / typed predicate+bbox。"""

    model_config = ConfigDict(extra="forbid")

    type: ScopeType = "full"
    ids: Optional[List[str]] = Field(default=None, max_length=500)
    """ids 范围：稳定 ID（feature.id 或 _wfid）显式列表。"""
    id_field: Optional[str] = Field(
        default=None, max_length=128,
        description="稳定 ID 字段；缺省自动探测 feature.id → properties._wfid",
    )
    predicate: Optional[Dict[str, Any]] = Field(
        default=None,
        description="typed 谓词（feature_pages.evaluate_where 兼容子集；同快照精确重放）",
    )
    bbox: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    selection_matched_count: Optional[int] = Field(
        default=None, ge=0,
        description="选择上下文的真实命中数（matched_count）；大于所携 ID 数即截断证据",
    )
    ids_truncated: Optional[bool] = Field(
        default=None,
        description="选择上下文的服务端派生截断标记（interaction IR ids_truncated）",
    )
    subset_ack: bool = False
    """ids 被截断且无法重放时的显式确认：仅交付所列子集（receipt 如实标注）。"""


class DeliveryCrsSpec(BaseModel):
    """目标坐标系与坐标政策（缺 CRS 不默认 4326）。"""

    model_config = ConfigDict(extra="forbid")

    target_crs: Optional[str] = Field(
        default=None, max_length=64,
        description='目标 CRS（如 "EPSG:4326"）；None = 不变换（格式要求除外）',
    )
    allow_unknown_source_crs: bool = False
    """源 CRS 未知且格式可承载未知 CRS 时的显式确认（GeoJSON 永不允许）。"""
    coordinate_precision: Optional[int] = Field(
        default=None, ge=6, le=15,
        description="GeoJSON 坐标小数位（显式有损选项，默认关闭=全精度）",
    )


class DeliverySpec(BaseModel):
    """交付规格 v1 —— 预览与执行的同一冻结输入。"""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    source: DeliverySourceRef
    scope: DeliveryScope = Field(default_factory=DeliveryScope)
    fields: DeliveryFieldSelection = Field(default_factory=DeliveryFieldSelection)
    crs: DeliveryCrsSpec = Field(default_factory=DeliveryCrsSpec)
    format: DeliveryFormat
    filename: str = Field(default="delivery", max_length=100)
    spreadsheet_safe: bool = False
    """CSV：对公式前缀（=,+,-,@,TAB,CR）做表格安全变换并记录（默认原样）。"""
    geometry_grouping: GeometryGrouping = "single"


class SourceFieldInfo(BaseModel):
    """源字段（类型为有界推断，标注 inferred）。"""

    name: str
    inferred_type: str = "unknown"
    sample_values: Optional[List[str]] = None


class SourceGeometrySummary(BaseModel):
    types: List[str] = Field(default_factory=list)
    has_z: bool = False
    null_or_empty_count: int = 0
    sampled_rows: int = 0


class SourceIdentity(BaseModel):
    """源身份与完整性证据（快照锚）。"""

    kind: SourceKind
    label: str = ""
    content_fingerprint: Optional[str] = None
    content_revision: Optional[int] = None
    artifact_revision_no: Optional[int] = None
    row_count: int = 0
    is_table: bool = False
    crs: Optional[str] = None
    crs_known: bool = False
    fields: List[SourceFieldInfo] = Field(default_factory=list)
    geometry: Optional[SourceGeometrySummary] = None
    completeness: Dict[str, Any] = Field(default_factory=dict)
    """截断/采样/总数证据（truncated/total_count/total_matching/sample…）。"""


class LossItem(BaseModel):
    """一条格式损失/降级声明（声明与实际改变一一对应，由 oracle 验证）。"""

    code: str
    message: str
    fields: Optional[List[str]] = None
    requires_ack: bool = False


class FormatCapability(BaseModel):
    """格式能力投影（来自真实驱动探测，非扩展名声明）。"""

    format: DeliveryFormat
    available: bool
    constrained: bool = False
    reason: Optional[str] = None
    extensions: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class DeliveryPreview(BaseModel):
    """POST /data-delivery/preview 返回 —— 执行前保真与范围协商。"""

    model_config = ConfigDict(extra="forbid")

    spec_fingerprint: str
    source: SourceIdentity
    requested_format: FormatCapability
    formats: List[FormatCapability] = Field(default_factory=list)
    estimated_rows: Optional[int] = None
    estimated_bytes: Optional[int] = None
    estimate_note: Optional[str] = None
    losses: List[LossItem] = Field(default_factory=list)
    blocking_issues: List[LossItem] = Field(default_factory=list)
    required_acknowledgments: List[str] = Field(default_factory=list)
    field_name_map_applied: Optional[Dict[str, str]] = None
    scope_note: Optional[str] = None
    downloadable_after: str = "completed"


class DeliveryMember(BaseModel):
    """交付包成员（多文件 ZIP/分层也有单一 manifest）。"""

    path: str
    role: str = "data"
    bytes: int = 0
    sha256: Optional[str] = None
    rows: Optional[int] = None


class DeliveryReceipt(BaseModel):
    """执行回执 —— 实际证据，非承诺。"""

    model_config = ConfigDict(extra="allow")

    delivery_id: str
    job_id: str
    format: DeliveryFormat
    status: str
    filename: str
    members: List[DeliveryMember] = Field(default_factory=list)
    row_count: int = 0
    bytes_total: int = 0
    content_sha256: Optional[str] = None
    crs: Dict[str, Any] = Field(default_factory=dict)
    """{source, target, transformed} —— transformed=False 时不得有坐标变换。"""
    losses_applied: List[str] = Field(default_factory=list)
    field_map_applied: Optional[Dict[str, str]] = None
    scope: Dict[str, Any] = Field(default_factory=dict)
    """{type, requested, delivered, missing_ids, subset_of_selection}。"""
    source: Dict[str, Any] = Field(default_factory=dict)
    """{kind, label, content_fingerprint, content_revision, artifact_revision_no}。"""
    writer_versions: Dict[str, str] = Field(default_factory=dict)
    data_object_id: Optional[str] = None
    durable: str = "unknown"
    copy_mode: str = "reencode"
    """byte_copy（GeoParquet 无变换安全复制）| reencode（经 writer 重编码）。"""
    artifact_ref: Optional[str] = None
    project_artifact_id: Optional[str] = None
    timings: Dict[str, float] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error: Optional[str] = None


class DeliveryJobCreateRequest(BaseModel):
    """POST /data-delivery/jobs 请求。"""

    model_config = ConfigDict(extra="forbid")

    spec: DeliverySpec
    acknowledged_losses: List[str] = Field(default_factory=list, max_length=64)
    project_id: Optional[str] = Field(
        default=None, max_length=128,
        description="登记交付成果到项目（可选；源为项目产物时推荐）",
    )


class DeliveryJobCreateResponse(BaseModel):
    job_id: str
    status: str
    idempotent_reuse: bool = False
    message: str = ""


class DeliveryJobView(BaseModel):
    """GET /data-delivery/jobs/{id} —— 状态 + 完成后的回执。"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: str
    progress: int = 0
    progress_message: Optional[str] = None
    stage: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    spec_fingerprint: Optional[str] = None
    format: Optional[DeliveryFormat] = None
    filename: Optional[str] = None
    receipt: Optional[DeliveryReceipt] = None
    download_url: Optional[str] = None
    error_code: Optional[str] = None
    error: Optional[str] = None


class DeliveryLimitsView(BaseModel):
    """交付硬帽（UI/工具同源的能力投影）。"""

    model_config = ConfigDict(extra="allow")

    max_rows_per_delivery: int
    max_output_bytes: int
    max_temp_disk_bytes: int
    max_fields: int
    max_ids: int
    read_batch_rows: int
    data_object_publish_budget_bytes: int


class DeliveryCapabilitiesResponse(BaseModel):
    """GET /data-delivery/capabilities —— 运行时真实驱动探测 + 硬帽 + 版本。"""

    model_config = ConfigDict(extra="forbid")

    formats: List[FormatCapability] = Field(default_factory=list)
    limits: DeliveryLimitsView
    writer_versions: Dict[str, str] = Field(default_factory=dict)
    cancel_endpoint: str = ""
