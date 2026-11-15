"""G44 业务字段合同 —— 持久实体（migration g44_field_contracts）。

两张表（纪律与 data_join_authoring.py / topology_workbench.py 一致：String PK +
uuid4；CHECK 词表与迁移 DDL 一字不差（drift 闸依赖）；行内 JSON 只存有界投影）。
不变式：**非 draft 行**（active/retired/superseded）内容永不可变 —— 修订=同
contract_key 新行；draft 行在编辑锁内允许原行内容更新（激活锁内 digest 复核
闭环依赖此边界）：

- ``field_contracts`` —— 版本化合同行。内容（rules JSON + digest）在**非 draft** 行不可变；
  修订 = 同 ``contract_key`` 组新行（revision+1）；active 指针由状态迁移 + 部分
  唯一索引保证每 (session_id, ref_id) 至多一行 active。
- ``field_contract_assessments`` —— 完整评估收据（job 关联 + verified 数据指纹/
  规则 digest + counts + findings 文件指针；激活只接受 completed 且零违反且指纹
  复核通过的收据）。

评估执行活性/取消/fencing 走既有 analysis_tasks durable job 事实源；findings 全量
分页在 DATA_DIR/field-contracts 终态包文件（原子写），不在行内存可写结果副本。
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
    Text,
    text,
)

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class FieldContract(Base):
    """版本化业务字段合同（一行 = 一个不可变内容版本）。"""

    __tablename__ = "field_contracts"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    #: 合同组身份（同一数据集绑定的演进链；修订生成新行、同 key、revision+1）
    contract_key = Column(String(36), nullable=False)
    org_id = Column(Integer, nullable=True)  # B 线占位
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    ref_id = Column(String(128), nullable=False)
    created_by = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False, default="")
    #: draft / active / retired / superseded
    status = Column(String(20), nullable=False, default="draft")
    #: 内容修订号（组内单调；CAS 令牌；行内容不可变）
    revision = Column(Integer, nullable=False, default=1)
    #: 合同文档 canonical JSON（ContractDocument；有界 ≤64KB 服务层保证）
    rules = Column(JSON, nullable=False)
    #: canonical digest（sha256 hex；激活/执行 read-set 锚点）
    normalized_digest = Column(String(64), nullable=False, default="")
    #: 绑定与激活证据（有界：fingerprint/content_revision/actor/at/assessment_id）
    binding = Column(JSON, nullable=False, default=dict)
    activation = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','retired','superseded')",
            name="ck_field_contracts_status",
        ),
        Index("ix_field_contracts_session_ref", "session_id", "ref_id"),
        Index("ix_field_contracts_key_rev", "contract_key", "revision", unique=True),
        # 每绑定至多一行 active（Postgres/SQLite 均支持部分唯一索引）
        Index(
            "uq_field_contracts_active_per_binding",
            "session_id",
            "ref_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class FieldContractAssessment(Base):
    """完整评估收据（一次评估一行；激活的证据面）。"""

    __tablename__ = "field_contract_assessments"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    contract_id = Column(String(36), nullable=False)
    contract_revision = Column(Integer, nullable=False)
    session_id = Column(String(255), nullable=False)
    ref_id = Column(String(128), nullable=False)
    #: queued / running / completed / cancelled / failed / incomplete
    status = Column(String(20), nullable=False, default="queued")
    #: durable job 关联（analysis_tasks.id；内联评估为空）
    job_id = Column(String(64), nullable=True)
    #: 评估对象快照（verified：data_digest/content_revision/features_total；
    #: rules_digest 与合同行 normalized_digest 一致才可激活）
    verified = Column(JSON, nullable=False, default=dict)
    counts = Column(JSON, nullable=False, default=dict)
    #: findings 分页文件指针 + digest（终态包；原子写）
    findings_path = Column(String(512), nullable=True)
    findings_digest = Column(String(64), nullable=True)
    #: 采样标记（sampled=True 的收据绝不可激活）
    sampled = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','cancelled','failed','incomplete')",
            name="ck_field_contract_assessments_status",
        ),
        Index("ix_fca_contract", "contract_id", "created_at"),
        Index("ix_fca_session_ref", "session_id", "ref_id"),
    )
