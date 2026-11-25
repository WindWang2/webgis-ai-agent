"""Cartographic Design Spec —— 制图设计规范实体（migration g50）。

WP1（goal-carto-design-series-20261125）：可复用专题制图设计规范的**权威
存储面**。规范是「设计 token / 模板组合的引用与约束文档」，不是第二
MapSpec、不是渲染器、不是组件/模型/资产库 —— 它只引用既有 registry 的
条目并声明数据角色与覆盖策略。

先例对齐（零新机制）：

- ``template_versions``（migration 0048）与 ``composition_template_assets``
  （0098）同款「不可变版本快照 + max+1 savepoint 重试 + 软废弃」语义，
  版本服务在 ``app/services/design_specs/versioning.py``；
- 租户可见性语义同 ``app/services/templates/scope.py``（builtin ∪ creator
  ∪ org；org_id IS NULL 不当 public）；
- 命名查重在服务层（同 G04 资产 ``conflict`` reason code 惯例），主表加
  ``uniq_scope_key`` 便于 deterministic 判定。

约定：领域表 String(36) PK；CHECK 词表与迁移一字不差。
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


class CartographyDesignSpec(Base):
    """设计规范主表（payload 恒指向当前 effective 版本；同模板先例）。"""

    __tablename__ = "cartography_design_specs"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位列（同 TemplateVersion）
    #: 规范名（scope 内唯一 —— 服务层查重 + uniq_scope_key 兜底）
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    #: builtin（内置案例规范，只读）| user（用户/组织规范）
    is_builtin = Column(Integer, nullable=False, default=0)
    creator_id = Column(String(255), nullable=True)
    #: 生命周期：draft | published | retired（同 G04 资产状态机词表）
    status = Column(String(20), nullable=False, default="draft")
    #: 当前 effective payload（最新版本深合并结果；兼容直读路径）
    payload = Column(JSON, nullable=False)
    current_version = Column(Integer, nullable=False, default=1)
    #: 结构 digest（canonical JSON sha256，随当前版本前移）
    structural_digest = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    #: 软退役（兼容读取继续可用，响应携带标记）
    retired_at = Column(DateTime, nullable=True)
    retirement_note = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_design_spec_status"
        ),
        UniqueConstraint("org_id", "creator_id", "name", name="uq_design_spec_scope_name"),
        Index("idx_design_spec_creator", "creator_id"),
        Index("idx_design_spec_status", "status"),
    )


class CartographyDesignSpecVersion(Base):
    """设计规范版本快照（不可变；继承链 + 引用校验投影 + 软废弃）。"""

    __tablename__ = "cartography_design_spec_versions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    spec_id = Column(
        String(36),
        ForeignKey("cartography_design_specs.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 单调递增版本号（同规范内 max+1；1 = 首版）
    version = Column(Integer, nullable=False)
    #: 该版本的 payload 快照（独立于主表）
    payload = Column(JSON, nullable=False)
    #: 继承：父版本（同规范内；跨规范父版本拒绝 —— 同 #1411 纪律）
    parent_version_id = Column(
        String(36),
        ForeignKey("cartography_design_spec_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 引用校验投影（对权威 registry 的 violation/warning 快照；有界）
    reference_violations = Column(JSON, nullable=True)
    reference_warnings = Column(JSON, nullable=True)
    #: 结构 digest（canonical JSON sha256）
    structural_digest = Column(String(80), nullable=True)
    deprecated_at = Column(DateTime, nullable=True)
    deprecation_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("spec_id", "version", name="uq_design_spec_version"),
        CheckConstraint("version >= 1", name="ck_design_spec_version_no"),
        Index("idx_design_spec_version_spec", "spec_id", "deprecated_at"),
    )


__all__ = ["CartographyDesignSpec", "CartographyDesignSpecVersion"]
