"""
PostgreSQL + PostGIS Domain Models for Enterprise Geospatial Data Fabric.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index, JSON, UniqueConstraint
)
from sqlalchemy.orm import relationship, backref
from app.core.database import Base


class DataSource(Base):
    """数据源配置与连接注册表"""
    __tablename__ = "data_sources"

    id = Column(String(255), primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    owner_id = Column(String(255), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False, index=True)
    endpoint_url = Column(Text, nullable=False)
    connection_profile = Column(JSON, nullable=False, default=dict)
    capabilities_json = Column(JSON, nullable=False, default=list)
    status = Column(String(50), default="active", index=True)
    last_health_check = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # #547：status 索引只保留 index=True 生成的 ix_data_sources_status ——
    # 之前 __table_args__ 里 idx_datasource_status 是同列第二份重复索引
    # （0011 建 idx_datasource_status、0017 又补了 ix_data_sources_status）。
    # uq_datasource_org_name 是迁移（0011）早已存在、模型此前缺漏的
    # 唯一约束 —— 补上让 ORM 识别、autogenerate 不再反复想 drop 它。
    __table_args__ = (
        Index("idx_datasource_org_type", "org_id", "source_type"),
        UniqueConstraint("org_id", "name", name="uq_datasource_org_name"),
    )


class DataFabricDataset(Base):
    """Spatial Catalog Lightweight Metadata Index table."""
    __tablename__ = "spatial_catalog_items"

    id = Column(String(255), primary_key=True)
    # #618-4: source_id / geometry_type 不再 index=True —— 左前缀已被
    # idx_catalog_source_name / idx_catalog_geom_feature 覆盖（0020 删 ix_*）。
    source_id = Column(String(255), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    geometry_type = Column(String(50), nullable=True)
    feature_type = Column(String(50), default="vector", index=True)
    crs = Column(String(50), default="EPSG:4326")
    bbox_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=False, default=list)
    descriptor_json = Column(JSON, nullable=False, default=dict)
    meta_profile_json = Column(JSON, nullable=False, default=dict)
    fingerprint = Column(String(255), nullable=True)
    # ADR-0094 §9（Catalog V2）：条目可用性。available=最近一次同步可见；
    # unavailable=源仍可达但该数据集已从源消失（保留元数据供 stale 检索）。
    availability = Column(String(32), nullable=False, default="available", server_default="available")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_catalog_source_name", "source_id", "name"),
        Index("idx_catalog_geom_feature", "geometry_type", "feature_type"),
        Index("idx_catalog_availability", "availability"),
    )

    # cascade="all, delete-orphan" mirrors the FK's ondelete="CASCADE": deleting a
    # DataSource removes its catalog items. Without it, SQLAlchemy's default
    # null-out-on-parent-delete trips the NOT NULL constraint on source_id.
    data_source = relationship(
        "DataSource",
        backref=backref("catalog_items", cascade="all, delete-orphan"),
        lazy="selectin",
    )


class DataMaterializationRecord(Base):
    """Materialization Audit & Provenance table."""
    __tablename__ = "materializations"

    id = Column(String(255), primary_key=True)
    # #618-4: dataset_id 不再 index=True —— 左前缀已被 idx_mat_dataset_ref 覆盖。
    dataset_id = Column(String(255), nullable=False)
    source_id = Column(String(255), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True)
    ref_id = Column(String(255), nullable=False, index=True)
    query_spec_json = Column(JSON, nullable=False, default=dict)
    fingerprint = Column(String(255), nullable=True)
    # ADR-0094 §43：查询指纹 + 结果模式（QueryEvidence 供 ADR-0092 lineage）。
    query_fingerprint = Column(String(64), nullable=True)
    result_mode = Column(String(32), nullable=True)
    record_count = Column(Integer, nullable=True)
    materialized_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_mat_dataset_ref", "dataset_id", "ref_id"),
    )


class DatasetStatisticsRecord(Base):
    """数据集统计持久化行（advisory，ADR-0101 D6）。

    统计是**性能提示，绝不是正确性真相**：查询结果永不依赖它。行按
    dataset 指纹 + 采集时间寻址；``expires_at`` 是有界保留（过期行由
    prune 清理；读取侧也拒绝过期行 —— stale 语义显式）。任何 DB 故障
    都 fail-open（回退进程内 TTL store / 描述符采集）。
    """

    __tablename__ = "dataset_statistics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_fingerprint = Column(String(64), nullable=False)
    source_type = Column(String(50), nullable=True)
    collector = Column(String(32), nullable=False, default="descriptor")
    confidence = Column(String(16), nullable=False, default="assumption")
    revision_strength = Column(String(16), nullable=False, default="weak")
    stats_json = Column(JSON, nullable=False, default=dict)
    collected_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_dataset_stats_fp_collected", "dataset_fingerprint", "collected_at"),
    )


class SourceFactsRecordModel(Base):
    """V7 SourceFacts 持久行（advisory，ADR-0119 W4）。

    与 ``dataset_statistics`` 的差异：**scope 进行**（scope_key + profile_id）
    —— 跨租户事实互不可见互不混写；payload 是完整 SourceFactsRecord
    （含 provenance/采样标注）。仍是性能提示不是正确性真相，fail-open。
    """

    __tablename__ = "data_fabric_source_facts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_key = Column(String(128), nullable=False)
    dataset_fingerprint = Column(String(64), nullable=False)
    profile_id = Column(String(64), nullable=True)
    collector = Column(String(32), nullable=False, default="descriptor")
    facts_json = Column(JSON, nullable=False, default=dict)
    collected_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_df_source_facts_scope_fp", "scope_key", "dataset_fingerprint", "collected_at"),
    )


class FederatedFeedbackModel(Base):
    """V7 分布式执行反馈持久行（advisory，ADR-0119 W11）。

    每行 = 一次链执行的 per-source 观测（行数/字节/时延/错误类/限流），
    scope 进行；进程内衰减中位数 + durable 样本双读。绝不含 secret。
    """

    __tablename__ = "data_fabric_federated_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_hash = Column(String(64), nullable=False)
    scope_key = Column(String(128), nullable=False)
    engine = Column(String(8), nullable=False, default="v6")
    outcome = Column(String(16), nullable=False, default="ok")
    payload_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_df_feedback_scope_plan", "scope_key", "plan_hash", "created_at"),
        Index("idx_df_feedback_created", "created_at"),
    )


# Aliases for backwards compatibility
DataSourceModel = DataSource
CatalogItemModel = DataFabricDataset
MaterializationModel = DataMaterializationRecord
DataFabricAuditLog = DataMaterializationRecord

__all__ = [
    "DataSource",
    "DataFabricDataset",
    "DataMaterializationRecord",
    "DataFabricAuditLog",
    "DataSourceModel",
    "CatalogItemModel",
    "MaterializationModel",
    "DatasetStatisticsRecord",
    "SourceFactsRecordModel",
    "FederatedFeedbackModel",
]
