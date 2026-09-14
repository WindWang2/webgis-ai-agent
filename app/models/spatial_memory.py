"""GIS Spatial Reasoning Memory 模型（方向 9，ADR-0183）。

``gis_spatial_memories`` —— GIS 世界事实的可复用记忆（resolved_place /
dataset 语义 / 字段角色 / CRS 结论 / 分析产物 ref / 成功策略 / provider
失败 / 产品决策 …），与 ADR-0069 的 ``carto_project_facts``（项目制图先验）
是**互补**关系：那边记「怎么画图」，这边记「GIS 世界是什么样」。

三条铁律（与 ADR-0069 同源，见 docs/dev/spatial-reasoning-memory-decisions.md）：

1. 作用域显式：``scope ∈ session|project|user`` + ``scope_id``；``org_id``
   恒由调用方（路由/收割位点）烙印，记录 payload 里声明租户一律无效；
2. 矛盾显式：同 key 的 active 事实语义变化走 **superseded 链**（保留
   ``supersedes_id`` 审计链），绝不静默 merge；partial unique index
   （active 行）在 DB 层兜底并发双写——同 key 永远至多一条 active；
3. 有界：按 (org, scope) 预算 LRU 淘汰 + ``expires_at`` 过期失效；
   value ≤ 2048 字符、refs 只存 ref 不存 payload（Zero Big Data in Context）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Column,
    text,
)

from app.models.project import Base


def _utcnow() -> datetime:
    # 仓库 DateTime 列统一 naive-UTC 语义（ADR-0069 评审结论：aware 绑定
    # 在 Postgres 上不可移植）——默认值同样 naive，防止旁路写入踩雷。
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GISSpatialMemory(Base):
    """一行 = 一条 GIS 空间记忆（active 供检索/注入；superseded/invalidated 留审计）。"""

    __tablename__ = "gis_spatial_memories"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 租户：恒由调用方烙印（effective org），anonymous 落 default 桶（ADR-0139）
    org_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    # session | project | user
    scope = Column(String(16), nullable=False)
    # session_id / project_id / 用户态键（user scope 时 = user_id）
    scope_id = Column(String(255), nullable=False)
    # resolved_place | boundary_ref | dataset_semantics | field_role |
    # crs_resolution | analysis_artifact | successful_strategy |
    # provider_failure | product_decision | user_cartographic_preference
    kind = Column(String(48), nullable=False)
    # 语义主体：规范地名 / dataset_key / 工具名 / 产品键 …（检索主键）
    subject = Column(String(255), nullable=False)
    # 有界 JSON（≤2048 字符，写入侧裁剪）；ref-only，绝无大数据 payload
    value = Column(JSON, nullable=False, default=dict)
    # ref-only JSON 数组：artifact ref / dataset ref / session ref
    refs = Column(JSON, nullable=False, default=list)
    # {source, method, turn_id?} —— 证据来源（closed vocab，写入门校验）
    evidence = Column(JSON, nullable=False, default=dict)
    # 语义指纹：同 key 不同指纹 = 矛盾 → supersede 链
    fingerprint = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False, default=0.0)
    # active | superseded | invalidated —— 只有 active 参与检索/注入
    status = Column(String(16), nullable=False, default="active", index=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    last_validated_at = Column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # 同 key 演化版本（每次再验证/取代 +1）
    version = Column(Integer, nullable=False, default=1)
    # 取代链：本行取代的直接前驱 id（superseded 行回填 own id 于后继）
    supersedes_id = Column(String(64), nullable=True)
    # 敏感记忆永不进入模型投影（API 可显式读取）
    sensitive = Column(Boolean, nullable=False, default=False)
    # ttl | dataset_version | manual | scope_gone —— GC/失效依据
    invalidation_rule = Column(String(32), nullable=False, default="ttl")

    __table_args__ = (
        CheckConstraint(
            "scope IN ('session','project','user')", name="ck_gis_mem_scope"
        ),
        CheckConstraint(
            "status IN ('active','superseded','invalidated')",
            name="ck_gis_mem_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_gis_mem_confidence"
        ),
        CheckConstraint(
            "invalidation_rule IN ('ttl','dataset_version','manual','scope_gone')",
            name="ck_gis_mem_invalidation",
        ),
        Index("idx_gis_mem_key", "org_id", "scope", "scope_id", "kind", "subject"),
        Index("idx_gis_mem_scope_id", "scope", "scope_id"),
        Index("idx_gis_mem_expires", "expires_at"),
        # 并发双写兜底（review F6）：同 key 至多一条 active 行——supersede
        # 竞态在 DB 层被判负方 IntegrityError，由 store 重试消解。
        Index(
            "uq_gis_mem_active_key",
            "org_id", "scope", "scope_id", "kind", "subject",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


__all__ = ["GISSpatialMemory"]
