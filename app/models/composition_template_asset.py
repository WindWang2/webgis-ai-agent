"""G04 —— 组合模板资产实体（migration 0098）。

ADR-0214 显式留白的「用户自定义 composition template 的 DB 存储面」：

- ``composition_template_assets`` —— 资产主表（scope/租户/生命周期状态；
  ``asset.*`` 命名空间 id，与系统 ``composition.*`` 模板物理不可撞）；
- ``composition_template_asset_versions`` —— 不可变版本快照（revision=
  max+1、发布门校验快照、指纹缓存、软退役）。

约定（照抄 0048 先例）：additive 新表 + create_all-coexistence guard 可重入
DDL + downgrade 反序；领域表 String(36) 版本行 PK；CHECK 词表与迁移一字
不差；不改动既有表（cartography_templates/template_versions 归样式模板线）。
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


class CompositionTemplateAsset(Base):
    """组合模板资产主表（生命周期状态 + scope/租户 + 溯源）。"""

    __tablename__ = "composition_template_assets"

    #: asset.<uuidhex>（lib 层强制命名空间；builtin 资产是代码定义不落库）
    id = Column(String(64), primary_key=True)
    org_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    creator_id = Column(
        String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: private（仅 creator）/ org（同组织）/ project（同项目成员）
    visibility = Column(String(16), nullable=False, default="private")
    #: visibility=project 时必填（服务层校验；FK 到 projects）
    project_id = Column(
        String(255), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    #: 生命周期状态机：draft → published → retired（retired 可 restore 复活）
    status = Column(String(16), nullable=False, default="draft")
    #: 最新版本号（与 versions 表 max(revision) 同步；0 = 尚无版本）
    latest_revision = Column(Integer, nullable=False, default=0)
    #: 当前已发布版本号（0 = 尚未发布；restore=旧版内容造新 revision 再发布）
    current_revision = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(
        DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'org', 'project')",
            name="ck_cta_visibility"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_cta_status"),
        CheckConstraint("latest_revision >= 0", name="ck_cta_latest_rev"),
        CheckConstraint("current_revision >= 0", name="ck_cta_current_rev"),
        Index("idx_cta_scope", "org_id", "creator_id", "status"),
        Index("idx_cta_project", "project_id", "status"),
    )


class CompositionTemplateAssetVersion(Base):
    """资产版本快照（不可变；发布门校验与指纹缓存随版本落库）。"""

    __tablename__ = "composition_template_asset_versions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    asset_id = Column(
        String(64),
        ForeignKey("composition_template_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 单调递增（同资产内 max+1；1 = 首版）
    revision = Column(Integer, nullable=False)
    #: TemplateAssetPayload.model_dump()（变体引用式或 inline）
    payload = Column(JSON, nullable=False)
    #: 版本状态：draft（草稿快照）→ published（发布门通过）→ retired（软退役）
    status = Column(String(16), nullable=False, default="draft")
    #: 发布门校验快照（空列表 = 通过；有界）
    validation = Column(JSON, nullable=True)
    #: 内容指纹缓存（asset_fingerprint；确定性可复算对账）
    fingerprint = Column(String(96), nullable=True)
    #: 版本级软退役（资产 retired 或该版本被显式退役；重放只读放行）
    retired_at = Column(DateTime, nullable=True)
    retired_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("asset_id", "revision", name="uq_cta_revision"),
        CheckConstraint("revision >= 1", name="ck_cta_revision_no"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_cta_version_status"),
        Index("idx_cta_version_asset", "asset_id", "status", "revision"),
    )


__all__ = [
    "CompositionTemplateAsset",
    "CompositionTemplateAssetVersion",
]
