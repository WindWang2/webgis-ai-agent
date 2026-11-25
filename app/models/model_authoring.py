"""模型作者草稿 ORM（Model Authoring Drafts）。

「不可变作者模型 vs 编译计划 vs 实例」三层中的**草稿层**：本表只保存
声明文档（ModelDocument dump）与 CAS 修订号；编译产物（不可变包）与
运行态分属既有 `workflow_packages` / workflow runtime 实例行 —— 本表
对发布仅持指针（latest_published_*），绝不复制执行态（INV-MA1）。
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


class ModelDraft(Base):
    """项目内可编辑模型草稿（project-scoped；CAS revision 乐观锁）。"""

    __tablename__ = "model_drafts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(
        String(255), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # ^[a-z][a-z0-9_]{2,40}$（service 层校验；项目内唯一）
    model_slug = Column(String(64), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    # ModelDocument dump（schema_version="1"；有界 ≤256KB 由 service 层校验）
    document = Column(JSON, nullable=False)
    # CAS 乐观锁：更新/删除必须携带 expected_revision；0 行更新 = 冲突
    revision_no = Column(Integer, nullable=False, default=1)
    # 声明指纹（layout 剔除后的 canonical sha256；科学语义变化必变）
    document_fingerprint = Column(String(64), nullable=False)
    source_kind = Column(String(20), nullable=False, default="manual")
    source_ref = Column(String(255), nullable=True)
    template_id = Column(String(120), nullable=True)
    # 发布指针（INV-MA1）：只记录最新发布的包 id/版本，不复制包内容
    latest_published_package_id = Column(String(64), nullable=True)
    latest_published_version = Column(String(16), nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "model_slug", name="uq_model_draft_project_slug"),
        Index("idx_model_draft_project_updated", "project_id", "updated_at"),
        Index("idx_model_draft_published_pkg", "latest_published_package_id"),
        CheckConstraint("revision_no >= 1", name="ck_model_draft_revision"),
        CheckConstraint(
            "source_kind IN ('manual','project_workflow','session_plan','template')",
            name="ck_model_draft_source_kind",
        ),
    )


__all__ = ["ModelDraft"]
