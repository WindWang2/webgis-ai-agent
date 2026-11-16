"""Linked Exploration（G46）—— 持久命名探索视图。

单表（伴随实体；迁移 g46_linked_exploration_views）：照抄 G38 data_join_plans
模式 —— String PK + uuid4；revision CAS 修改；行内 JSON 只存有界计划
（plan ≤32KB 由服务层保证），完整匹配集永不入行（运行期由 G24 where-scope
selection handle 承载，导出走冻结编译链）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
)
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class ExplorationView(Base):
    """命名探索视图：作者计划（源锚 + 图表 + 条件）+ 预览证据。

    - 保存不隐含换当前地图；应用（apply）不改源数据。
    - 源锚（ref_id/content_revision/content_fingerprint）在重开时核对：
      漂移 → 服务层返回 stale（要求重新预览/重新绑定），不无声绑定新数据。
    - revision：创建 = 1；每次内容修改 +1（CAS 令牌，E25 并发冲突面）。
    """

    __tablename__ = "exploration_views"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False, default="")
    #: draft / archived（archived = 作者显式归档，可恢复）
    status = Column(String(20), nullable=False, default="draft")
    revision = Column(Integer, nullable=False, default=1)
    #: ExplorationPlan.model_dump（有界 ≤32KB 由服务层保证）
    plan = Column(JSON, nullable=False)
    normalized_digest = Column(String(64), nullable=False, default="")
    #: 源锚冗余列（快速 stale 判定，不重复 plan 内权威）
    source_ref = Column(String(255), nullable=False)
    source_revision = Column(Integer, nullable=False)
    source_fingerprint = Column(String(128), nullable=True)
    #: 最新预览证据（有界 ≤8KB：匹配计数/分母口径/digest；服务层保证）
    preview = Column(JSON, nullable=True)
    preview_digest = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_exploration_view_revision"),
        CheckConstraint(
            "status IN ('draft','archived')", name="ck_exploration_view_status"),
        CheckConstraint("source_revision >= 1", name="ck_exploration_view_src_rev"),
        Index("idx_exploration_views_session", "session_id", "created_at"),
    )
