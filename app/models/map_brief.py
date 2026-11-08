"""G11 —— 证据化地图简报表（migration 0104）。

两层存储：``map_briefs`` 会话作用域可编辑草稿（MapBriefSpec JSON +
revision 单调，所有权随会话）；``map_brief_releases`` 不可变发布版本
（manifest + content_sha256 幂等键 + CAS html_object_key + 分享列）。
release 的 html/evidence 真身在内容寻址存储；brief 行删除后 release
保留（BRF-O11），归属经冗余 conversation_id 解析。约定照抄
story_map_asset（0099）先例：载荷有界性在服务层校验，表不做 JSON
形状断言。
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


class MapBrief(Base):
    """会话的简报草稿（可编辑；revision 单调递增）。"""

    __tablename__ = "map_briefs"

    id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    conversation_id = Column(
        String(255),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(255), nullable=False, default="地图简报")
    #: MapBriefSpec 的 JSON 序列（服务层限 ≤512KB）
    spec = Column(Text, nullable=False)
    #: 编辑代次（每次保存 +1；乐观并发 expected_revision 比对基准）
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_mb_conversation"),
        CheckConstraint("revision >= 1", name="ck_mb_revision"),
    )


class MapBriefRelease(Base):
    """简报的不可变发布版本（插入后只允许更新 share_* 列）。"""

    __tablename__ = "map_brief_releases"

    id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    #: 草稿删除后保留（SET NULL）；归属由 conversation_id 冗余解析。
    brief_id = Column(
        String(64),
        ForeignKey("map_briefs.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id = Column(
        String(255),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    release_no = Column(Integer, nullable=False)
    frozen_revision = Column(Integer, nullable=False)
    #: ready|failed —— 生成失败的文件不列 ready（BRF-O05）
    status = Column(String(16), nullable=False, default="ready")
    #: 发布 manifest JSON（files/evidence/dataset_versions/指纹/draft 标记）
    manifest = Column(Text, nullable=False)
    #: canonical 发布载荷内容哈希 —— 同输入重复提交幂等返回既有行。
    content_sha256 = Column(String(64), nullable=False)
    #: CAS html 载荷键（内容寻址；下载经所有权校验后代理读）
    html_object_key = Column(String(255), nullable=False)
    share_code = Column(String(64), nullable=True, index=True)
    #: naive UTC（F-04 纪律：naive 列存 UTC，读取侧换算）
    share_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("brief_id", "release_no", name="uq_mbr_brief_release"),
        UniqueConstraint("content_sha256", name="uq_mbr_content"),
        UniqueConstraint("share_code", name="uq_mbr_share_code"),
    )


__all__ = ["MapBrief", "MapBriefRelease"]
