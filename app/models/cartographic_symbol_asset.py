"""G40 —— 制图符号资产实体（migration g40_cartographic_symbol_assets）。

可复用点符号/面纹理资产的持久面（照 composition_template_asset / 0098 先例）：

- ``cartographic_symbol_assets`` —— 资产主表（scope/租户/生命周期状态；
  ``cartoasset.`` 命名空间 id；builtin 资产是代码定义不落库）；
- ``cartographic_symbol_asset_versions`` —— 不可变版本快照（revision=
  max+1、内容摘要缓存、软退役）。规范形 JSON 存版本行（有界
  ≤512KB），与 content blob（durable BlobStore，sha256 密钥）双轨：
  blob 供下载/回读校验，JSON 供服务内确定性消费。

约定照抄 0098：additive 新表 + create_all-coexistence guard 可重入 DDL +
downgrade 反序；CHECK 词表与迁移一字不差；不改动既有表。
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


class CartographicSymbolAsset(Base):
    """制图符号资产主表（生命周期状态 + scope/租户 + 溯源）。"""

    __tablename__ = "cartographic_symbol_assets"

    #: cartoasset.<uuidhex>（服务层强制命名空间；builtin 是代码定义不落库）
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
    #: visibility=project 时必填（服务层校验）
    project_id = Column(
        String(255), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    #: point_symbol | area_texture（资产种类创建后不可变）
    kind = Column(String(32), nullable=False)
    #: draft → published → retired（retire 软退役：新绑定拒绝、钉版本重放放行）
    status = Column(String(16), nullable=False, default="draft")
    latest_revision = Column(Integer, nullable=False, default=0)
    current_revision = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'org', 'project')",
            name="ck_csa_visibility"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_csa_status"),
        CheckConstraint(
            "kind IN ('point_symbol', 'area_texture')",
            name="ck_csa_kind"),
        CheckConstraint("latest_revision >= 0", name="ck_csa_latest_rev"),
        CheckConstraint("current_revision >= 0", name="ck_csa_current_rev"),
        Index("idx_csa_scope", "org_id", "creator_id", "status"),
        Index("idx_csa_project", "project_id", "status"),
        Index("idx_csa_kind", "kind", "status"),
    )


class CartographicSymbolAssetVersion(Base):
    """资产版本快照（不可变；规范形 + 内容摘要 + 软退役）。"""

    __tablename__ = "cartographic_symbol_asset_versions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    asset_id = Column(
        String(64),
        ForeignKey("cartographic_symbol_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 单调递增（同资产内 max+1；1 = 首版）
    revision = Column(Integer, nullable=False)
    #: SymbolAssetDocument.model_dump()（规范形 JSON；有界 ≤512KB）
    document = Column(JSON, nullable=False)
    #: 版本状态：draft → published → retired
    status = Column(String(16), nullable=False, default="draft")
    #: 规范形内容摘要（content_digest；确定性可复算对账）
    content_digest = Column(String(96), nullable=False)
    #: BlobStore 键（sha256 hex；规范形 canonical 字节的 CAS 键）
    blob_key = Column(String(64), nullable=False)
    #: 导入源摘要（origin/license/note 的 bounded 快照；冗余于规范形 source）
    source_note = Column(Text, nullable=True)
    retired_at = Column(DateTime, nullable=True)
    retired_note = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("asset_id", "revision", name="uq_csa_revision"),
        CheckConstraint("revision >= 1", name="ck_csa_revision_no"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_csa_version_status"),
        Index("idx_csa_version_asset", "asset_id", "status", "revision"),
    )
