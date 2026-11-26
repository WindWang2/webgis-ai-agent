"""Cartographic Map Series —— 系列地图作者计划实体（migration g51）。

WP3（goal-carto-design-series-20261125）：系列地图的**引用与计划面**。
主表登记成员（各自 session 的权威 MapSpec 引用）、设计规范 pin、可比性
契约与输出预算；版本快照表同 g50 先例（不可变 + max+1 savepoint 重试 +
继承链 + 软废弃）。系列**不承载任何地图状态** —— 渲染/数据权威分属
MapSpec lifecycle 与数据会话。

约定：领域表 String(36) PK；CHECK 词表与迁移一字不差；``design_spec_id``
跨库引用（WP1 权威）—— 无 FK 约束，解析时点校验（软废弃 pin 兼容）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class CartographyMapSeries(Base):
    """系列主表（payload 恒指向当前 effective 计划；同 design spec 先例）。"""

    __tablename__ = "cartography_map_series"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_builtin = Column(Integer, nullable=False, default=0)
    creator_id = Column(String(255), nullable=True)
    #: 生命周期：draft | published | retired（同 G04/design spec 状态机词表）
    status = Column(String(20), nullable=False, default="draft")
    #: 设计规范 pin（WP1 权威；跨库 —— 无 FK 约束，解析时点校验）
    design_spec_id = Column(String(36), nullable=True)
    #: 版本 pin（缺省 = 绑定时 latest 的解析时点记录；NULL = 未记录）
    spec_version = Column(Integer, nullable=True)
    #: 当前 effective 计划（map_series.v1 typed；members 有界 ≤50）
    payload = Column(JSON, nullable=False)
    current_version = Column(Integer, nullable=False, default=1)
    structural_digest = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    retired_at = Column(DateTime, nullable=True)
    retirement_note = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_map_series_status"
        ),
        UniqueConstraint("org_id", "creator_id", "name", name="uq_map_series_scope_name"),
        Index("idx_map_series_creator", "creator_id"),
        Index("idx_map_series_status", "status"),
        Index("idx_map_series_spec_pin", "design_spec_id"),
    )


class CartographyMapSeriesVersion(Base):
    """系列版本快照（不可变；继承链 + 软废弃；同 g50 版本语义）。"""

    __tablename__ = "cartography_map_series_versions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    series_id = Column(
        String(36),
        ForeignKey("cartography_map_series.id", ondelete="CASCADE"),
        nullable=False,
    )
    version = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    parent_version_id = Column(
        String(36),
        ForeignKey("cartography_map_series_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    structural_digest = Column(String(80), nullable=True)
    deprecated_at = Column(DateTime, nullable=True)
    deprecation_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("series_id", "version", name="uq_map_series_version"),
        CheckConstraint("version >= 1", name="ck_map_series_version_no"),
        Index("idx_map_series_version_series", "series_id", "deprecated_at"),
    )


__all__ = ["CartographyMapSeries", "CartographyMapSeriesVersion"]
