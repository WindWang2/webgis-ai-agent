"""交互式只读地图发布（04 线）——版本化发布快照表。

一张已验证的 MapSpec 面向读者的发布事实源：
- ``product_id`` 稳定产品 ID（跨版本不变），``version`` 递增 —— 旧版本发布后
  不可变，作者编辑原图后需显式发布新版本（不隐式改变）。
- ``status`` draft → published → revoked（撤销终态可见）；过期由
  ``share_expires_at`` 判定（naive UTC，同 ``Report.share_expires_at`` F-04）。
- ``access_policy`` 默认 ``private``；``link`` = 可撤销只读链接（share_code）。
  匿名公开可发现面不在本期词表。
- ``snapshot`` JSON 冻结发布快照（``publication_snapshot.v1``，见
  ``app/services/publication/contract.py``）：冻结 MapSpec 文档 + 公开字段
  白名单 + 许可/署名 + 可访问性清单 + QA 摘要。发布后不可变。
- 归属三元组 ``creator_id + org_id + owner_token``（ADR-0052 范式）；
  owner_token 经 ``OwnerTokenColumn`` 单向摘要存储。

红线：snapshot 不含会话完整历史、私有 ref 载荷、owner_token、provider key、
内部绝对路径（contract 层负向扫描强制）。
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, JSON, DateTime, Index, CheckConstraint, UniqueConstraint,
)
from app.core.database import Base
from app.core.owner_tokens import OwnerTokenColumn

#: 与 ck_map_publication_status 一致；「撤销/过期」是读者可见终态。
ALLOWED_PUBLICATION_STATUSES = ("draft", "published", "revoked")
#: 与 ck_map_publication_access 一致；新增策略属破坏性契约变更。
ALLOWED_ACCESS_POLICIES = ("private", "link")

PUBLICATION_STATUS_DRAFT = "draft"
PUBLICATION_STATUS_PUBLISHED = "published"
PUBLICATION_STATUS_REVOKED = "revoked"


def utcnow_naive() -> datetime:
    """统一 naive UTC（DateTime 列存 UTC 的仓内惯例，F-04）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MapPublication(Base):
    """交互式只读地图发布快照（一个版本一行）。"""
    __tablename__ = "map_publications"

    id = Column(String(36), primary_key=True)
    #: 稳定产品 ID：同一地图的多次发布共享，version 递增。
    product_id = Column(String(36), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    #: 源会话（发布时刻的出处；发布后读者访问不依赖会话存活）。
    session_id = Column(String(255), nullable=False)
    title = Column(String(300), nullable=False)
    status = Column(String(20), nullable=False, default=PUBLICATION_STATUS_DRAFT)
    access_policy = Column(String(20), nullable=False, default="private")
    #: 可撤销只读链接凭证（token_urlsafe；唯一索引防枚举碰撞降级）。
    share_code = Column(String(64), unique=True, nullable=True)
    share_expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    #: 源 MapSpec 指纹（canonical-JSON sha256，与 mapspec store sidecar 同算法）。
    spec_fingerprint = Column(String(80), nullable=False)
    #: 发布时刻的源 mutation revision（freshness/ stale 提示用）。
    spec_revision = Column(Integer, nullable=True)
    #: 冻结发布快照（publication_snapshot.v1；发布后不可变）。
    snapshot = Column(JSON, nullable=False)
    #: 物化产物目录就绪标记（published 必为 True；恢复/巡检可见）。
    materialized = Column(Integer, nullable=False, default=0)
    #: 归属三元组（owner_token 单向摘要列）。
    creator_id = Column(String(255), nullable=True)
    org_id = Column(String(64), nullable=True)
    owner_token = Column(OwnerTokenColumn(), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_map_publication_product", "product_id", "version"),
        Index("idx_map_publication_session", "session_id"),
        Index("idx_map_publication_status", "status"),
        CheckConstraint(
            "status IN ('draft', 'published', 'revoked')",
            name="ck_map_publication_status",
        ),
        CheckConstraint(
            "access_policy IN ('private', 'link')",
            name="ck_map_publication_access",
        ),
        CheckConstraint("version >= 1", name="ck_map_publication_version"),
        # 同一产品版本的发布幂等约束：重复发布同一 fingerprint 复用同一行。
        UniqueConstraint("product_id", "version", name="uq_map_publication_version"),
    )


__all__ = [
    "MapPublication",
    "ALLOWED_PUBLICATION_STATUSES",
    "ALLOWED_ACCESS_POLICIES",
    "PUBLICATION_STATUS_DRAFT",
    "PUBLICATION_STATUS_PUBLISHED",
    "PUBLICATION_STATUS_REVOKED",
    "utcnow_naive",
]
