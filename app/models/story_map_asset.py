"""G3D05 —— StoryMap 会话资产表（migration 0099）。

ADR-0275 D6：StoryMap 此前 compile/export 全无状态，「可恢复的项目
资产」缺口由本表补上 —— 会话作用域单行（conversation 唯一），保存
最新 StoryMapSpec 快照 + 单调 revision；所有权经会话归属守卫（与
messages 同生命周期 CASCADE）。约定照抄 0098 先例：additive 新表 +
create_all-coexistence guard 可重入 DDL；spec 载荷有界性在服务层
校验（表不做 JSON 形状断言）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StoryMapAsset(Base):
    """会话的 StoryMap 最新快照（revision 单调递增）。"""

    __tablename__ = "story_map_assets"

    id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    conversation_id = Column(
        String(255),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(255), nullable=False, default="StoryMap 专报")
    #: StoryMapSpec 的 JSON 序列（服务层限 ≤512KB；schema_version ≥1.0）
    spec = Column(Text, nullable=False)
    #: 保存代次（每次 save +1；播放端据此判断资产新旧）
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # R1-P2（评审修复）：与 migration 0099 同名同形（create_all 引导的
    # 库与迁移库约束一致；此前 inline unique=True 无名且缺 CHECK）。
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_sma_conversation"),
        CheckConstraint("revision >= 1", name="ck_sma_revision"),
    )


__all__ = ["StoryMapAsset"]
