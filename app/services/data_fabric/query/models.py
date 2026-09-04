"""QuerySpec V2 + QueryPlan + capability + evidence models (ADR-0094).

QuerySpecV2 是唯一的结构化查询输入真理：legacy ``QuerySpec``（schemas 层）
经 ``normalize.py`` 归一化为该模型。所有模型可序列化、确定性
（canonical dict → fingerprint）。
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.data_fabric.query.predicates import (
    Predicate,
    SpatialPredicate,
    TemporalPredicate,
    _check_field_name,
)

MAX_PAGE_LIMIT = 10_000
DEFAULT_PAGE_LIMIT = 100


class ResultMode(str, Enum):
    DESCRIPTOR = "descriptor"          # 仅元数据，零物化
    STATISTICS = "statistics"          # 仅 count/聚合 + evidence，零 geometry 传输
    SAMPLE = "sample"                  # 确定性采样（seed 派生自 dataset fingerprint）
    FEATURES = "features"              # 有界 inline GeoJSON 特征
    MATERIALIZE = "materialize"        # 物化为 ref_id
    VECTOR_TILE = "vector_tile"        # z/x/y tile 流（服务器 tile 优先）


# ── 分页 ────────────────────────────────────────────────────────────────────


class OffsetPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["offset"] = "offset"
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    offset: int = Field(default=0, ge=0, le=100_000_000)


class CursorPage(BaseModel):
    """keyset 分页。cursor 为不透明令牌（适配器生成，通常是编码后的排序键）。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["cursor"] = "cursor"
    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    cursor: Optional[str] = None  # None = 第一页


PageSpec = Union[CursorPage, OffsetPage]


# ── 聚合 / 排序 ─────────────────────────────────────────────────────────────


class AggSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    func: Literal["count", "sum", "avg", "min", "max", "stddev", "distinct_count"]
    field: Optional[str] = None  # count 可无字段

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return _check_field_name(v)
        return v


class OrderByItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    direction: Literal["asc", "desc"] = "asc"

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        return _check_field_name(v)


# ── 输出 / 采样 / 预算 ──────────────────────────────────────────────────────


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ResultMode = ResultMode.FEATURES
    crs: str = "EPSG:4326"  # 输出 CRS（geometry 序列化坐标系）
    max_features: Optional[int] = Field(default=None, ge=1, le=200_000)
    max_bytes: Optional[int] = Field(default=None, ge=1)


class SampleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: int = Field(ge=1, le=5_000)
    seed: Optional[int] = None  # None → 派生自 dataset fingerprint（确定性）
    method: Literal["reservoir", "first"] = "reservoir"


class ExecutionBudget(BaseModel):
    """硬预算（planner 与执行器双重强制）。"""

    model_config = ConfigDict(extra="forbid")
    deadline_s: float = Field(default=30.0, ge=1.0, le=600.0)
    max_rows: int = Field(default=50_000, ge=1, le=1_000_000)
    max_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    max_vertices: int = Field(default=50_000_000, ge=1_000)
    max_pages: int = Field(default=200, ge=1, le=10_000)


# ── QuerySpecV2 ─────────────────────────────────────────────────────────────


class QuerySpecV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    select: Optional[List[str]] = None  # None = 全部属性字段（geometry 依 mode）
    filter: Optional[Predicate] = None
    spatial: Optional[SpatialPredicate] = None
    temporal: Optional[TemporalPredicate] = None
    aggregate: Optional[List[AggSpec]] = Field(default=None, min_length=1)
    group_by: Optional[List[str]] = None
    distinct: bool = False
    order_by: List[OrderByItem] = Field(default_factory=list)
    page: PageSpec = Field(default_factory=OffsetPage)
    output: OutputSpec = Field(default_factory=OutputSpec)
    sample: Optional[SampleSpec] = None
    execution: ExecutionBudget = Field(default_factory=ExecutionBudget)

    @field_validator("select", "group_by")
    @classmethod
    def _v_fields(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        for f in v:
            _check_field_name(f)
        return v

    def canonical_dict(self) -> Dict[str, Any]:
        """确定性序列化（fingerprint 用；AND/OR 已在谓词层 canonical 化）。"""
        from app.services.data_fabric.query.predicates import predicate_to_canonical_dict

        d: Dict[str, Any] = {}
        if self.select is not None:
            d["select"] = sorted(set(self.select))
        if self.filter is not None:
            d["filter"] = predicate_to_canonical_dict(self.filter)
        if self.spatial is not None:
            d["spatial"] = predicate_to_canonical_dict(self.spatial)
        if self.temporal is not None:
            d["temporal"] = predicate_to_canonical_dict(self.temporal)
        if self.aggregate:
            d["aggregate"] = [a.model_dump() for a in self.aggregate]
        if self.group_by:
            d["group_by"] = sorted(set(self.group_by))
        if self.distinct:
            d["distinct"] = True
        if self.order_by:
            d["order_by"] = [o.model_dump() for o in self.order_by]
        d["page"] = {
            "kind": self.page.kind,
            "limit": self.page.limit,
            # cursor 语义上属于"下一页位置"，进入 fingerprint 会破坏缓存复用；
            # offset 属于查询语义（同 offset 必须命中同结果）→ 保留。
            **({"offset": self.page.offset} if self.page.kind == "offset" else {}),
            **({"cursor_pos": self.page.cursor} if (self.page.kind == "cursor" and self.page.cursor) else {}),
        }
        out = self.output.model_dump()
        d["output"] = {"mode": out["mode"], "crs": out["crs"]}
        if self.sample is not None:
            d["sample"] = self.sample.model_dump(exclude={"seed"})
        d["execution"] = {
            "max_rows": self.execution.max_rows,
            "max_bytes": self.execution.max_bytes,
        }
        return d


def query_fingerprint(spec: QuerySpecV2, dataset_fingerprint: Optional[str] = None) -> str:
    payload = json.dumps(
        spec.canonical_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    combined = f"{dataset_fingerprint or '-'}:{payload}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


# ── Capability Matrix V2（truthful）─────────────────────────────────────────


class AdapterCapabilitiesV2(BaseModel):
    """结构化 capability 契约。声明即契约：AdapterContractTest 验证每一项。"""

    model_config = ConfigDict(extra="forbid")

    source_type: str
    bbox_pushdown: bool = False
    filter_pushdown: bool = False             # AST → 参数化编译
    projection_pushdown: bool = False
    sort_pushdown: bool = False
    offset_pagination: bool = False
    cursor_pagination: bool = False
    spatial_predicates: List[str] = Field(default_factory=list)  # 支持的操作名
    temporal_filter: bool = False
    aggregation: bool = False
    group_by: bool = False
    count: bool = False                       # count-only 零 geometry
    statistics: bool = False                  # min/max/avg 等统计下推
    server_reprojection: bool = False
    vector_tiles: bool = False
    range_requests: bool = False
    streaming: bool = False
    max_page_size: int = 10_000
    server_side_spatial_join: bool = False    # 同源 server-side join 可用

    def supports_spatial_op(self, op: str) -> bool:
        return op in self.spatial_predicates


# ── QueryPlan ───────────────────────────────────────────────────────────────


class ExecutionFragment(BaseModel):
    """单源执行片段（人类可读 + 结构化）。"""

    step: str                    # e.g. "postgis_pushdown", "local_spatial_filter"
    description: str
    pushed: bool = False


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 标识
    source_type: str
    source_id: Optional[str] = None
    dataset_id: str
    dataset_fingerprint: Optional[str] = None
    query_fingerprint: Optional[str] = None

    # 划分
    normalized_query: Dict[str, Any] = Field(default_factory=dict)
    pushed_filters: List[str] = Field(default_factory=list)      # 谓词摘要
    local_filters: List[str] = Field(default_factory=list)
    pushed_projection: bool = False
    pushed_spatial: bool = False
    pushed_aggregation: bool = False
    pushed_sort: bool = False
    pagination_strategy: Literal["cursor", "offset", "single_page", "none"] = "offset"
    pagination_note: Optional[str] = None

    # 估算（确定性）
    estimated_rows: Optional[int] = None
    estimated_bytes: Optional[int] = None

    execution_mode: Literal["pushdown", "local_fallback", "hybrid"] = "pushdown"
    result_mode: ResultMode = ResultMode.FEATURES
    fallback_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    steps: List[ExecutionFragment] = Field(default_factory=list)

    def summary_lines(self) -> List[str]:
        """explain 输出（不含 secret/连接信息）。"""
        lines = [
            f"Source: {self.source_type}",
            f"Dataset: {self.dataset_id}",
            "",
            "Pushdown:",
            f"  bbox             {'YES' if self.pushed_spatial else 'no'}",
            f"  filter           {'YES' if self.pushed_filters else 'no'}",
            f"  projection       {'YES' if self.pushed_projection else 'no'}",
            f"  aggregation      {'YES' if self.pushed_aggregation else 'no'}",
            f"  sort             {'YES' if self.pushed_sort else 'no'}",
            "",
            f"Local: {', '.join(self.local_filters) if self.local_filters else 'none'}",
            f"Pagination: {self.pagination_strategy}"
            + (f" ({self.pagination_note})" if self.pagination_note else ""),
            f"Estimated rows: {self.estimated_rows if self.estimated_rows is not None else 'unknown'}",
            f"Result mode: {self.result_mode.value}",
        ]
        if self.fallback_reason:
            lines.append(f"Reason: {self.fallback_reason}")
        for w in self.warnings:
            lines.append(f"Warning: {w}")
        return lines


# ── DatasetVersion / QueryEvidence ──────────────────────────────────────────


class DatasetVersion(BaseModel):
    """轻量版本记录。revision_strength 诚实标注：远端拿不到真实 revision 时
    为 weak，不假装 immutable。"""

    model_config = ConfigDict(extra="forbid")

    descriptor_fingerprint: Optional[str] = None
    schema_fingerprint: Optional[str] = None
    content_hint: Optional[str] = None        # e.g. etag / last_modified / count
    source_revision: Optional[str] = None
    observed_at: Optional[str] = None         # ISO-8601
    revision_strength: Literal["strong", "weak"] = "weak"


class QueryEvidence(BaseModel):
    """执行证据（附加在 QueryResult.metadata 与 materializations 行内）。

    不建第二 lineage store：这是 ADR-0092 artifact lineage 的供数侧输入。
    """

    model_config = ConfigDict(extra="forbid")

    query_id: Optional[str] = None
    dataset_id: Optional[str] = None
    source_id: Optional[str] = None
    dataset_fingerprint: Optional[str] = None
    query_fingerprint: Optional[str] = None
    normalized_query: Optional[Dict[str, Any]] = None
    pushdowns: Dict[str, bool] = Field(default_factory=dict)
    local_operations: List[str] = Field(default_factory=list)
    result_count: Optional[int] = None
    total_matching: Optional[int] = None
    truncated: bool = False
    execution_duration_s: Optional[float] = None
    fallbacks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    dataset_version: Optional[DatasetVersion] = None
    rows_fetched: Optional[int] = None        # 远端实际传输行数（pushdown_ratio 分母）
    rows_returned: Optional[int] = None
    http_requests: Optional[int] = None
    db_queries: Optional[int] = None
    cache_hit: Optional[bool] = None
    retry_count: Optional[int] = None


__all__ = [
    "ResultMode",
    "OffsetPage",
    "CursorPage",
    "PageSpec",
    "AggSpec",
    "OrderByItem",
    "OutputSpec",
    "SampleSpec",
    "ExecutionBudget",
    "QuerySpecV2",
    "query_fingerprint",
    "AdapterCapabilitiesV2",
    "ExecutionFragment",
    "QueryPlan",
    "DatasetVersion",
    "QueryEvidence",
    "MAX_PAGE_LIMIT",
    "DEFAULT_PAGE_LIMIT",
]
