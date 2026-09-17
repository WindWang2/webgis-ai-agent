"""Project Knowledge Projection 模型（方向：Project Knowledge Projection）。

``project_knowledge_entries`` —— 项目级空间知识的**投影/索引**读模型：
区域、数据集版本、方法、Mission、产物、地图成品、失败模式、偏好的
可检索、可失效、可复用组织视图。

三条铁律（与 gis_memory / ADR-0069 同源）：

1. **借来的身份**：本表不是第二个权威 store —— 每行语义身份 =
   ``(authority_store, authority_id, version_token)``，全部回指既有权威
   （project_datasets / artifacts / map_products / workflows / gis_missions /
   gis_spatial_memories / carto_project_facts / session refs）。删除投影行
   绝不影响权威行；权威源重建（rebuild）幂等且充分。
2. **失效显式**：权威版本前进 → ``stale``；权威行消失/detach →
   ``invalidated``（scope_gone）；同 natural key 换 token → 旧行
   ``superseded``（保留 ``supersedes_id`` 审计链）。partial unique index
   在 DB 层兜底：同 key 至多一条 active。
3. **有界**：每 (org, project) 行数预算（默认 400，淘汰序与 gis_memory
   同纪律）；summary ≤300 字符；refs ≤8 个 ref-tag（只存 id+token，
   绝无 payload）；org_id 恒由调用方烙印。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
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

from app.core.database import Base

_ENTITY_KINDS_SQL = (
    "'dataset_version','artifact','map_product','workflow','method',"
    "'mission','failure_pattern','place','preference'"
)
_AUTHORITY_STORES_SQL = (
    "'project_dataset','artifact','map_product','workflow','mission',"
    "'gis_memory','carto_fact','session_ref'"
)
_STATUSES_SQL = "'active','stale','superseded','invalidated'"
_RULES_SQL = "'','version_bump','head_changed','claim_lost','manual','scope_gone'"


def _utcnow() -> datetime:
    # 仓库 DateTime 列统一 naive-UTC 语义（ADR-0069 评审结论）。
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProjectKnowledgeEntry(Base):
    """一行 = 一条项目知识投影（active 供检索/复用；stale/superseded/invalidated 留审计）。"""

    __tablename__ = "project_knowledge_entries"

    id = Column(String(64), primary_key=True, default=lambda: f"pkx_{uuid.uuid4().hex[:16]}")
    # 租户：恒由调用方（经项目 auth 门触发的 rebuild/读路径）烙印 effective org。
    org_id = Column(String(255), nullable=False, index=True)
    project_id = Column(String(255), nullable=False)
    # 实体类别（closed vocab，见 contract.ENTITY_KINDS）
    entity_kind = Column(String(32), nullable=False)
    # 语义主体：数据集名 / 产物名 / 地名 / capability:algorithm …（展示用；
    # 判定只看指纹与作用域 —— 名字相似永不升级复用判定）
    subject = Column(String(255), nullable=False)
    # 权威回指（borrowed identity）
    authority_store = Column(String(48), nullable=False)
    authority_id = Column(String(255), nullable=False)
    # 索引时观察到的权威版本指纹（fingerprint / sha256 / revision 字符串）
    version_token = Column(String(128), nullable=False, default="")
    # 单行摘要（写入口截断 ≤300 字符；ref-first，无 payload）
    summary = Column(String(300), nullable=False, default="")
    # 作用域：AOI 包围盒 [minx,miny,maxx,maxy]（权威源携带时才写；NULL=未知）
    bbox = Column(JSON, nullable=True)
    # 作用域：时间标签（如 "2024" / "2024-Q1"；NULL=未知）
    temporal_label = Column(String(64), nullable=True)
    # 方法键（"capability:algorithm"，method 匹配用；NULL=未知）
    method_key = Column(String(200), nullable=True)
    # 关系 ref-tag 数组（≤8；{relation,authority,id,token}）—— 权威关系的
    # 有界投影，不是第二张关系图
    refs = Column(JSON, nullable=False, default=list)
    # 投影证据（{seam, observed_at}，closed：indexer/ref_hook/manual）
    evidence = Column(JSON, nullable=False, default=dict)
    # active | stale | superseded | invalidated —— 只有 active 参与检索/复用
    status = Column(String(16), nullable=False, default="active", index=True)
    # version_bump | head_changed | claim_lost | manual | scope_gone
    invalidation_rule = Column(String(32), nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    last_validated_at = Column(DateTime, default=_utcnow, nullable=False)
    # 同 key 演化版本（每次再验证/取代 +1）
    version = Column(Integer, nullable=False, default=1)
    # 取代链：本行取代的直接前驱 id
    supersedes_id = Column(String(64), nullable=True)
    # 排序权重（recency/重要性的有界先验；检索 order_by 用）
    weight = Column(Float, nullable=False, default=0.0)

    __table_args__ = (
        CheckConstraint(
            f"entity_kind IN ({_ENTITY_KINDS_SQL})",
            name="ck_pkx_entity_kind",
        ),
        CheckConstraint(
            f"authority_store IN ({_AUTHORITY_STORES_SQL})",
            name="ck_pkx_authority_store",
        ),
        CheckConstraint(
            f"status IN ({_STATUSES_SQL})",
            name="ck_pkx_status",
        ),
        CheckConstraint(
            f"invalidation_rule IN ({_RULES_SQL})",
            name="ck_pkx_invalidation_rule",
        ),
        Index("idx_pkx_org_project_kind_status", "org_id", "project_id", "entity_kind", "status"),
        Index("idx_pkx_org_project_authority", "org_id", "project_id", "authority_store", "authority_id"),
        # 并发双写兜底：同 natural key 至多一条 active 行 —— supersede 竞态
        # 在 DB 层判负方 IntegrityError（与 gis_memory 同纪律）。
        Index(
            "uq_pkx_active_key",
            "org_id", "project_id", "entity_kind", "authority_store", "authority_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


__all__ = ["ProjectKnowledgeEntry"]
