"""Data Catalog V3 —— 统一数据目录（§十七/§十八；只读联邦，零第二写路径）。

V3 之前「找数据」要跨四个互不相通的元数据宇宙（审计 Agent A/E）：
session 产物账本、UploadRecord（DB）、fabric SpatialCatalogService
（进程内、重启即失）、ProjectDataset（DB）。Agent 只能靠逐个工具
拼凑，或更糟——扫文件目录。

本模块是**只读联邦目录**：

- 各来源经 V3 契约投影成统一 ``CatalogEntry``；
- 过滤轴（§十七）：keyword / category / role / crs / bbox 相交 /
  field 存在性 / status / lifecycle / tags；
- 结果有界：``limit`` 封顶 + ``truncated`` 显式声明 + 按 updated_at
  排序（时间缺失当 epoch 0，不冒充最新）；
- 来源故障**诚实披露**：``sources_queried`` 记录每个来源的计数与
  错误 —— DB 不可用不是空结果（audit 结论「fetch failed ≠ empty」
  的目录版）；
- 不写任何来源的状态：写路径仍归 artifact_registry / upload /
  fabric / project 各自所有。

DB 来源（uploads / project datasets）是 best-effort：引擎不可用或
表缺失 → 该来源记 error，其余来源照常返回。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_MAX_ENTRIES_SCANNED = 2000          # 单来源扫描上限（目录不是全量导出器）
_MAX_FIELD_NAMES = 32
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CatalogEntry(BaseModel):
    """目录条目（统一投影；有界字段集）。"""

    entry_id: str                          # scope 内唯一（artifact_id / upload_id / dataset id）
    scope: str                             # session / upload / fabric / project
    name: str = ""
    category: str = "unknown"
    artifact_subtype: str = ""
    logical_role: str = ""
    lifecycle: str = ""
    status: str = ""                       # 来源原生状态（session 5 态等）
    crs: str = ""
    extent: Optional[List[float]] = None
    feature_count: Optional[int] = None
    size_bytes: Optional[int] = None
    fields: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    source_type: str = ""
    source_ref: str = ""
    produced_by: str = ""
    stable_identity: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: List[str]) -> List[str]:
        return [str(f)[:128] for f in v[:_MAX_FIELD_NAMES]]

    @field_validator("tags")
    @classmethod
    def _bounded_tags(cls, v: List[str]) -> List[str]:
        return [str(t)[:64] for t in v[:16]]

    @field_validator("extent")
    @classmethod
    def _extent_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None or len(v) != 4:
            return None
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None


class SourceStatus(BaseModel):
    """来源查询披露（诚实原则）。"""

    source: str
    count: int = 0
    error: str = ""


class CatalogResult(BaseModel):
    """目录查询结果（有界 + 截断声明 + 来源披露）。"""

    entries: List[CatalogEntry] = Field(default_factory=list)
    total_matched: int = 0
    truncated: bool = False
    sources_queried: List[SourceStatus] = Field(default_factory=list)

    def summaries(self, *, max_entries: int = 20) -> List[Dict[str, Any]]:
        """LLM 视图（§三十二）：每条目一个紧凑摘要。"""
        return [
            {
                "id": e.entry_id,
                "scope": e.scope,
                "name": e.name or None,
                "type": e.category,
                "role": e.logical_role or None,
                "lifecycle": e.lifecycle or None,
                "crs": e.crs or None,
                "extent": e.extent,
                "feature_count": e.feature_count,
                "fields_sample": e.fields[:8] or None,
            }
            for e in self.entries[:max_entries]
        ]


# ── 过滤器（§十七查询轴；纯函数）─────────────────────────────────────


class CatalogFilter(BaseModel):
    """目录过滤器（全部条件 AND 语义；None = 不过滤该轴）。"""

    keyword: str = ""                       # 命中 name/subtype/source_ref/produced_by
    category: str = ""                      # 粗类 token
    role: str = ""                          # LogicalRole
    crs: str = ""
    bbox: Optional[List[float]] = None      # 相交测试（[minx,miny,maxx,maxy]）
    field: str = ""                         # 字段名存在性
    status: str = ""
    lifecycle: str = ""
    tags: List[str] = Field(default_factory=list)   # 任一命中
    scope: str = ""                         # session / upload / fabric / project

    @field_validator("bbox")
    @classmethod
    def _bbox_shape(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is None:
            return None
        if len(v) != 4:
            raise ValueError("bbox must be [minx, miny, maxx, maxy]")
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            raise ValueError("bbox values must be numeric")

    def matches(self, entry: CatalogEntry) -> bool:
        if self.scope and entry.scope != self.scope:
            return False
        if self.category and entry.category != self.category:
            return False
        if self.role and entry.logical_role != self.role:
            return False
        if self.crs and entry.crs != self.crs:
            return False
        if self.status and entry.status != self.status:
            return False
        if self.lifecycle and entry.lifecycle != self.lifecycle:
            return False
        if self.field and self.field not in entry.fields:
            return False
        if self.tags and not (set(t.lower() for t in entry.tags) & set(
            t.lower() for t in self.tags
        )):
            return False
        if self.bbox is not None:
            if entry.extent is None:
                return False  # 无法判定 ≠ 匹配（bbox 查询只返回可判定者）
            if not _bbox_intersects(self.bbox, entry.extent):
                return False
        if self.keyword:
            kw = self.keyword.lower()
            haystack = " ".join(
                filter(None, [entry.name, entry.artifact_subtype, entry.source_ref,
                              entry.produced_by, entry.entry_id])
            ).lower()
            if kw not in haystack:
                return False
        return True


def _bbox_intersects(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ── 来源适配器（只读投影）───────────────────────────────────────────


def _entry_from_artifact_record(record: Any) -> CatalogEntry:
    from app.lib.data.artifact_contract import from_artifact_record

    c = from_artifact_record(record)
    md = getattr(record, "metadata", None) or {}
    md = md if isinstance(md, dict) else {}
    field_names: List[str] = []
    fs = md.get("field_schema")
    if isinstance(fs, dict):
        field_names = [str(k) for k in list(fs.keys())[:_MAX_FIELD_NAMES]]
    return CatalogEntry(
        entry_id=c.artifact_id,
        scope="session",
        name=str(md.get("display_name") or c.artifact_subtype or c.artifact_id),
        category=c.artifact_type,
        artifact_subtype=c.artifact_subtype,
        logical_role=c.logical_role.value,
        lifecycle=c.lifecycle.value,
        status=str(getattr(record, "status", "") or ""),
        crs=c.crs,
        extent=c.extent,
        feature_count=c.feature_count,
        size_bytes=c.size_bytes,
        fields=field_names,
        tags=[str(t) for t in (md.get("tags") or [])] if isinstance(md.get("tags"), list) else [],
        source_type=c.source.source_type,
        source_ref=c.source.source_ref,
        produced_by=c.produced_by.tool or c.produced_by.capability,
        stable_identity=c.stable_identity,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _entry_from_upload_record(row: Any) -> CatalogEntry:
    file_type = str(getattr(row, "file_type", "") or "")
    category = (
        "raster" if file_type == "raster"
        else "vector" if file_type == "vector"
        else "archive"
    )
    fields: List[str] = []
    meta = getattr(row, "meta_json", None)
    if isinstance(meta, dict):
        attrs = meta.get("attrs") or meta.get("fields")
        if isinstance(attrs, list):
            fields = [str(a) for a in attrs[:_MAX_FIELD_NAMES]]
    return CatalogEntry(
        entry_id=f"upload:{getattr(row, 'id', '')}",
        scope="upload",
        name=str(getattr(row, "original_name", "") or ""),
        category=category,
        crs=str(getattr(row, "crs", "") or ""),
        extent=(
            list(row.bbox) if isinstance(getattr(row, "bbox", None), list)
            and len(row.bbox) == 4 else None
        ),
        feature_count=getattr(row, "feature_count", None),
        size_bytes=getattr(row, "file_size", None),
        fields=fields,
        source_type="upload",
        source_ref=str(getattr(row, "filename", "") or ""),
        tags=["upload"],
        lifecycle="available",
        updated_at=getattr(row, "upload_time", None),
        created_at=getattr(row, "upload_time", None),
    )


def _entry_from_fabric_descriptor(desc: Any) -> CatalogEntry:
    from app.schemas.data_fabric_schema import DatasetDescriptor

    d = desc if isinstance(desc, DatasetDescriptor) else DatasetDescriptor.model_validate(desc)
    schema_fields = [str(k) for k in list(d.schema_fields.keys())[:_MAX_FIELD_NAMES]]
    return CatalogEntry(
        entry_id=f"fabric:{d.id}",
        scope="fabric",
        name=str(d.title or d.name or d.id),
        category="vector" if (d.data_type or "vector") == "vector" else str(d.data_type),
        fields=schema_fields or [str(f.get("name", "")) for f in d.fields if isinstance(f, dict)][:_MAX_FIELD_NAMES],
        crs=str(d.crs or d.srs or ""),
        extent=d.bbox if isinstance(d.bbox, list) and len(d.bbox) == 4 else None,
        feature_count=d.feature_count,
        source_type=f"fabric:{d.source_type}",
        source_ref=str(d.source_id or d.id),
        tags=["fabric", str(d.source_type)],
        lifecycle="available",
    )


class DataCatalog:
    """联邦只读目录。"""

    def __init__(self) -> None:
        self._session_records_loader = self._load_session_records  # 测试可替换

    # ── 来源加载 ────────────────────────────────────────────────────
    async def _load_session_records(self, session_id: str) -> List[Any]:
        from app.services.artifact_registry import list_artifacts

        return await list_artifacts(session_id)

    def _load_fabric_entries(self) -> Tuple[List[CatalogEntry], str]:
        try:
            from app.services.data_fabric.spatial_catalog import spatial_catalog_service

            descriptors = spatial_catalog_service.list_datasets()[:_MAX_ENTRIES_SCANNED]
            return [_entry_from_fabric_descriptor(d) for d in descriptors], ""
        except Exception as e:  # noqa: BLE001 — 来源故障诚实披露
            return [], f"fabric catalog unavailable: {e}"

    def _load_upload_entries(self, session_id: str) -> Tuple[List[CatalogEntry], str]:
        try:

            from app.models.upload import UploadRecord
            from app.tools._utils import db_session

            with db_session() as db:
                rows = (
                    db.query(UploadRecord)
                    .filter(UploadRecord.session_id == session_id)
                    .order_by(UploadRecord.upload_time.desc())
                    .limit(_MAX_ENTRIES_SCANNED)
                    .all()
                )
                return [_entry_from_upload_record(r) for r in rows], ""
        except Exception as e:  # noqa: BLE001
            return [], f"uploads unavailable: {e}"

    # ── 主查询 ──────────────────────────────────────────────────────
    async def search(
        self,
        *,
        session_id: Optional[str] = None,
        filter_: Optional[CatalogFilter] = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> CatalogResult:
        """联邦查询（§十七）。limit 封顶 _MAX_LIMIT；按 updated_at 降序。"""
        flt = filter_ or CatalogFilter()
        limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
        entries: List[CatalogEntry] = []
        sources: List[SourceStatus] = []

        # 1) session 产物账本
        if session_id and (not flt.scope or flt.scope == "session"):
            try:
                records = (await self._session_records_loader(session_id))[:_MAX_ENTRIES_SCANNED]
                scoped = [_entry_from_artifact_record(r) for r in records]
                entries.extend(scoped)
                sources.append(SourceStatus(source="session", count=len(scoped)))
            except Exception as e:  # noqa: BLE001
                sources.append(SourceStatus(source="session", error=str(e)))

        # 2) uploads（DB；best-effort）
        if session_id and (not flt.scope or flt.scope == "upload"):
            up_entries, err = self._load_upload_entries(session_id)
            entries.extend(up_entries)
            sources.append(
                SourceStatus(source="uploads", count=len(up_entries), error=err)
            )

        # 3) fabric 目录（进程内；best-effort）
        if not flt.scope or flt.scope == "fabric":
            fab_entries, err = self._load_fabric_entries()
            entries.extend(fab_entries)
            sources.append(
                SourceStatus(source="fabric", count=len(fab_entries), error=err)
            )

        matched = [e for e in entries if flt.matches(e)]
        matched.sort(
            key=lambda e: e.updated_at or e.created_at or _EPOCH,
            reverse=True,
        )
        total = len(matched)
        return CatalogResult(
            entries=matched[:limit],
            total_matched=total,
            truncated=total > limit,
            sources_queried=sources,
        )

    async def describe(
        self, session_id: Optional[str], entry_id: str
    ) -> Optional[CatalogEntry]:
        """单条目查询（§十八 describe_artifact 的目录侧）。"""
        result = await self.search(
            session_id=session_id, filter_=CatalogFilter(keyword=entry_id), limit=_MAX_LIMIT
        )
        for e in result.entries:
            if e.entry_id == entry_id:
                return e
        return None


_catalog: Optional[DataCatalog] = None


def get_data_catalog() -> DataCatalog:
    global _catalog
    if _catalog is None:
        _catalog = DataCatalog()
    return _catalog


def reset_data_catalog() -> None:
    global _catalog
    _catalog = None
