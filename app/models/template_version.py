"""Templates V9 —— 模板版本化实体（migration 0048）。

P4（任务书 §2）把 118 行薄壳 templates 服务升格的**版本事实面**：

- ``template_versions`` —— 模板每次变更的不可变快照（payload 快照 +
  parent_version_id 继承链 + component_refs 组件引用投影 + deprecated
  失效标记）；
- ``cartography_templates`` 上的继承/失效以**版本行**承载（零列变更于
  既有表 —— 避免与并行线改同一表；迁移 0048 只 additive 新表）；
- 组件引用对齐 Cartography V7 组件注册表（``component_registry``）：
  校验在服务层（versioning.py），引用快照落版本行。

约定：领域表 String(36) PK；CHECK 词表与迁移一字不差；org_id 为 B 线
占位列。
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


class TemplateVersion(Base):
    """模板版本快照（不可变；继承链 + 组件引用 + 失效标记）。"""

    __tablename__ = "template_versions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位列
    template_id = Column(
        String(255),
        ForeignKey("cartography_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 单调递增版本号（同模板内 max+1；1 = 首版）
    version = Column(Integer, nullable=False)
    #: 该版本的 payload 快照（独立于主表 payload —— 主表始终指向"当前"）
    payload = Column(JSON, nullable=False)
    #: 继承：父版本（可属于父模板）——覆盖语义 = 子 payload 深合并于父
    parent_version_id = Column(
        String(36),
        ForeignKey("template_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: 组件引用投影（Cartography V7 对齐；服务层校验后快照）
    component_refs = Column(JSON, nullable=True)
    #: 校验违规快照（创建时点；有界）
    component_violations = Column(JSON, nullable=True)
    #: 失效迁移：deprecated 标记（兼容读取继续可用，响应带标记）
    deprecated_at = Column(DateTime, nullable=True)
    deprecation_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        # 同模板内版本号唯一（0035 先例：唯一性走 UniqueConstraint，不用
        # unique=True Index —— 避免 Postgres 上约束索引与显式索引同名冲突）
        UniqueConstraint("template_id", "version", name="uq_template_version"),
        CheckConstraint("version >= 1", name="ck_template_version_no"),
        Index("idx_template_version_template", "template_id", "deprecated_at"),
    )


__all__ = ["TemplateVersion"]
