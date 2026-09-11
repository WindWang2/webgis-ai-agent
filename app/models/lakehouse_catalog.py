"""Lakehouse catalog model — Spatial Lakehouse V7 (ADR-0119, Scope F).

**投影而非事实源**：事实源 = DataObject manifest（BlobStore）+ 会话台账
（artifact_registry）+ 修订台账（artifact_revisions）。本表是它们的
**可检索投影**（owner 域、bbox/时间、tags、producer、分页游标）——
可随时由事实源重建（reindex）；GC 对账以 manifest 可解析性为准
（R0-10：行 revoked ≠ 字节删除，字节判定永远回到 manifest）。

多 owner 发布（R0-7）：同一 data_object（同 content_sha256）可发布进
多个 project / 同时活在 session —— 代理 PK（uuid），唯一性约束在
``(owner_type, owner_id, content_sha256)``。

索引设计（查询有结构预算 —— scope F"no unbounded catalog response"）：
- owner + created_at 复合（域内时间线）；
- bbox 用 (minx, maxx)/(miny, maxy) 复合（范围谓词走索引）；
- tags GIN **仅 PostgreSQL**（``json`` 列无 GIN opclass —— R0-25，
  用 JSONB variant）；SQLite（测试方言）跳过，查询侧 tags 过滤为
  有界行集上的内存过滤。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.core.database import Base

#: JSON 列类型：PG 用 JSONB（GIN opclass 要求），其余 JSON。
CatalogJSON = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class LakehouseCatalogItem(Base):
    """lakehouse 对象的可检索投影行（owner 域隔离；事实源见模块 docstring）。"""

    __tablename__ = "lakehouse_catalog_items"

    id = Column(String(36), primary_key=True, default=_uuid)
    #: DataObject id（64 hex）或 ref id（ref:cube/<id> 等 cursor 形态）。
    object_id = Column(String(255), nullable=False)
    owner_type = Column(String(20), nullable=False)
    owner_id = Column(String(128), nullable=False)
    #: ADR-0139 租户作用域（organizations.id 字符串原文）；目录检索的
    #: 租户硬边界（与 owner 域谓词叠加）。
    org_id = Column(String(255), nullable=False)
    kind = Column(String(32), nullable=False)
    title = Column(String(255), nullable=True)
    producer_capability = Column(String(128), nullable=True)
    producer_tool = Column(String(128), nullable=True)
    workflow_run_id = Column(String(64), nullable=True)
    tags_json = Column(CatalogJSON, nullable=False, default=list)
    descriptor_json = Column(CatalogJSON, nullable=False, default=dict)
    minx = Column(Float, nullable=True)
    miny = Column(Float, nullable=True)
    maxx = Column(Float, nullable=True)
    maxy = Column(Float, nullable=True)
    time_start = Column(DateTime, nullable=True)
    time_end = Column(DateTime, nullable=True)
    content_sha256 = Column(String(64), nullable=False)
    byte_size = Column(BigInteger().with_variant(Integer(), "sqlite"),
                       nullable=False, default=0)
    #: active | revoked（撤销 = tombstone；既有引用仍可解析，检索默认不可见）。
    status = Column(String(20), nullable=False, default="active",
                    server_default="active")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "content_sha256",
                         name="uq_lh_cat_owner_content"),
        Index("idx_lh_cat_owner_created", "owner_type", "owner_id", "created_at"),
        Index("idx_lh_cat_org_created", "org_id", "created_at"),
        Index("idx_lh_cat_kind", "kind"),
        Index("idx_lh_cat_status", "status"),
        Index("idx_lh_cat_time_start", "time_start"),
        Index("idx_lh_cat_time_end", "time_end"),
        Index("idx_lh_cat_bbox_x", "minx", "maxx"),
        Index("idx_lh_cat_bbox_y", "miny", "maxy"),
        Index("idx_lh_cat_object", "object_id"),
        # PG-only GIN（表达式级 jsonb opclass；SQLite 方言自动跳过）。
        Index("idx_lh_cat_tags_gin", "tags_json", postgresql_using="gin"),
        CheckConstraint("status IN ('active','revoked')",
                        name="ck_lh_cat_status"),
        CheckConstraint("owner_type IN ('session','project')",
                        name="ck_lh_cat_owner_type"),
    )

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
            "kind": self.kind,
            "title": self.title,
            "producer_capability": self.producer_capability,
            "producer_tool": self.producer_tool,
            "workflow_run_id": self.workflow_run_id,
            "tags": list(self.tags_json or []),
            "bbox": (
                [self.minx, self.miny, self.maxx, self.maxy]
                if None not in (self.minx, self.miny, self.maxx, self.maxy)
                else None
            ),
            "time_start": self.time_start.isoformat() if self.time_start else None,
            "time_end": self.time_end.isoformat() if self.time_end else None,
            "content_sha256": self.content_sha256,
            "byte_size": int(self.byte_size or 0),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
