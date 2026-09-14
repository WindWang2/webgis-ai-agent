"""ADS（adaptive data supply）fabric 账本模型 —— 迁移 0070/0071 的模型面。

迁移 0070/0071（ads-v1 线，ADR-0175/0178）建的三张账本表此前只有
migration 侧定义（访问走 raw SQL）。模型↔迁移 drift 闸
（tests/test_deploy_migration_wiring.py::test_migrated_schema_matches_models）
要求迁移链产物在 ``Base.metadata`` 有对应声明 —— 本模块补齐**忠实镜像**
（列集/唯一约束/索引与迁移 DDL 一字不差；词表 CHECK 同款）：

- ``ads_acquisition_snapshots``：版本钉扎快照（0070）；
- ``ads_acquisition_facts``：取数事实 + 成本预算（0071）；
- ``ads_cost_budgets``：per-source 预算上限（0071）。

只声明、不迁移 —— schema 的唯一事实源仍是迁移链（0070/0071 已入库）。
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.core.database import Base


class AdsAcquisitionSnapshot(Base):
    """版本钉扎快照（0070；uq(dataset_key, pin)）。"""

    __tablename__ = "ads_acquisition_snapshots"
    __table_args__ = (
        UniqueConstraint("dataset_key", "pin", name="uq_ads_snapshot_dataset_pin"),
    )

    id = Column(String(32), primary_key=True)
    dataset_key = Column(String(256), nullable=False, index=True)
    pin = Column(String(128), nullable=False)
    version_token = Column(String(128), nullable=False)
    content_fingerprint = Column(String(64), nullable=True)
    schema_fingerprint = Column(String(64), nullable=True)
    schema_fields_json = Column(Text, nullable=True)
    revision_json = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )


class AdsAcquisitionFact(Base):
    """取数事实账本（0071；request/dataset_key 索引）。"""

    __tablename__ = "ads_acquisition_facts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','degraded','failed')", name="ck_ads_fact_outcome"
        ),
    )

    id = Column(String(32), primary_key=True)
    request_id = Column(String(128), nullable=False, index=True)
    dataset_key = Column(String(256), nullable=False, index=True)
    source_id = Column(String(128), nullable=True)
    version = Column(String(128), nullable=False)
    rows = Column(Integer, nullable=False)
    bytes = Column(BigInteger, nullable=False)
    latency_ms = Column(Float, nullable=False)
    retries = Column(Integer, nullable=False)
    degraded = Column(Boolean, nullable=False)
    outcome = Column(String(16), nullable=False)
    fallback_json = Column(Text, nullable=True)
    drift = Column(String(32), nullable=True)
    wave = Column(String(16), nullable=False)
    ts = Column(DateTime(timezone=True), server_default="now()", nullable=False)


class AdsCostBudget(Base):
    """per-source 预算上限（0071；uq(source_id, request_type, wave)）。"""

    __tablename__ = "ads_cost_budgets"
    __table_args__ = (
        UniqueConstraint("source_id", "request_type", "wave", name="uq_ads_budget_scope"),
    )

    id = Column(String(32), primary_key=True)
    source_id = Column(String(128), nullable=False, index=True)
    request_type = Column(String(64), nullable=False)
    max_rows = Column(Integer, nullable=True)
    max_bytes = Column(BigInteger, nullable=True)
    max_ms = Column(Float, nullable=True)
    max_quota = Column(Float, nullable=True)
    wave = Column(String(16), nullable=False)
