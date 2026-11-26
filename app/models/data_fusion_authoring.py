"""Data Fusion Authoring —— 持久实体（migration 0110）。

三张表（伴随实体；执行活性/取消/fencing 全部走既有 analysis_tasks
事实源，不另造任务状态机；内容走 DATA_DIR/data-fusion + DataObject
CAS，不在行内存可写结果副本）：

- ``data_fusion_plans``     —— 版本化融合计划（作者态；revision CAS）；
- ``data_fusion_runs``      —— 一次匹配执行（durable job 关联 + 有界
                              收据 + 匹配包指针；commit 前的中间态）；
- ``data_fusion_decisions`` —— 审阅决定（按 plan + pair 稳定键唯一；
                              CAS 修改；人工锁定跨刷新保留）。

纪律（与 data_join_authoring.py 一致）：String PK + uuid4；CHECK 词表
与迁移 DDL 一字不差（drift 闸依赖）；行内 JSON 只存有界投影。
决定挂 plan（非 run）：增量刷新新 run 自动继承锁定决定（重挂靠靠
pair_key 内容指纹，与快照 index 无关）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class DataFusionPlan(Base):
    """融合计划（草稿保存不执行、不改源、不改 MapSpec）。"""

    __tablename__ = "data_fusion_plans"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False, default="")
    #: draft / archived
    status = Column(String(20), nullable=False, default="draft")
    #: 计划修订号：创建 = 1；每次内容修改 +1（CAS 令牌）
    revision = Column(Integer, nullable=False, default=1)
    #: 计划 JSON（FusionPlan.model_dump；有界 ≤32KB 由服务层保证）
    plan = Column(JSON, nullable=False)
    #: 规范化 digest（预览/执行/幂等键锚点）
    normalized_digest = Column(String(64), nullable=False, default="")
    #: 双源内容指纹（执行幂等键成分；预览时更新）
    fingerprint_a = Column(String(128), nullable=True)
    fingerprint_b = Column(String(128), nullable=True)
    #: 最新预览证据（有界 ≤8KB：计数/basis/样本数；服务层保证）
    preview = Column(JSON, nullable=True)
    #: 预览针对的 digest（计划或源变化 → 失效）
    preview_digest = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_data_fusion_plan_revision"),
        CheckConstraint(
            "status IN ('draft','archived')",
            name="ck_data_fusion_plan_status"),
        Index("idx_data_fusion_plans_session", "session_id", "created_at"),
    )


class DataFusionRun(Base):
    """一次融合匹配执行（作业层状态与收据分离；commit 前为中间态）。

    ``output_ref`` 仅在 commit 后写入（不可变融合数据集版本）；
    ``bundle`` 指针（match 包路径哈希）在 run 完成即有。
    """

    __tablename__ = "data_fusion_runs"

    id = Column(String(40), primary_key=True)
    plan_id = Column(
        String(36), ForeignKey("data_fusion_plans.id", ondelete="CASCADE"),
        nullable=False)
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    #: 执行时的计划修订与 digest（重放对账锚点）
    plan_revision = Column(Integer, nullable=False)
    plan_digest = Column(String(64), nullable=False)
    #: pending / running / completed / failed / cancelled
    status = Column(String(20), nullable=False, default="pending")
    #: durable job id（analysis_tasks.id，BigInteger —— #2208 同款防溢出）
    job_id = Column(BigInteger().with_variant(Integer, "sqlite"),
                    nullable=True)
    #: 幂等键（与 durable job 同键；并发收敛锚点）
    idempotency_key = Column(String(255), nullable=True)
    #: 增量刷新基底 run（首跑 NULL；影响面/锁定继承的追溯链）
    base_run_id = Column(String(40), nullable=True)
    #: 有界收据（匹配计数、审阅队列摘要、源版本、诊断、错误码）
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    #: commit 后的不可变融合数据集版本
    output_ref = Column(String(255), nullable=True)  # ref:data-fusion/<run>
    output_sha256 = Column(String(128), nullable=True)
    output_features = Column(Integer, nullable=True)
    #: commit 收据（字段裁决摘要/质量报告投影；有界）
    commit_receipt = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_data_fusion_run_status"),
        CheckConstraint("plan_revision >= 1",
                        name="ck_data_fusion_run_plan_rev"),
        Index("idx_data_fusion_runs_idem", "idempotency_key", "created_at"),
        Index("idx_data_fusion_runs_session", "session_id", "created_at"),
        Index("idx_data_fusion_runs_plan", "plan_id", "created_at"),
    )


class DataFusionDecision(Base):
    """审阅决定（plan 级持久；pair 稳定键唯一；CAS 修改）。

    ``pair_key`` = "fp:<fa>:<fb>"（两端实体内容指纹有序拼接）—— 与
    快照 index 无关，来源刷新后按内容重挂靠；``idx_a/idx_b`` 只是
    决定时的展示锚（decision_run 快照序）。
    ``locked`` = True 表示人工锁定：匹配/刷新绝不无声推翻（决定按
    pair_key 内容过滤继承；解除锁定唯一路径是显式 delete 决定）。
    """

    __tablename__ = "data_fusion_decisions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    plan_id = Column(
        String(36), ForeignKey("data_fusion_plans.id", ondelete="CASCADE"),
        nullable=False)
    session_id = Column(String(255), nullable=False)
    #: "fp:<fa>:<fb>" 稳定键
    pair_key = Column(String(160), nullable=False)
    #: accept（合并）/ reject（保持分开）
    action = Column(String(20), nullable=False)
    #: 人工字段值覆盖（unresolved 字段的显式决定；有界 ≤16KB）
    field_values = Column(JSON, nullable=True)
    #: manual 几何采纳侧（a/b；geometry adoption=manual 时必填）
    geometry_side = Column(String(4), nullable=True)
    #: 决定时参照的 run 与展示锚
    decision_run_id = Column(String(40), nullable=True)
    idx_a = Column(Integer, nullable=True)
    idx_b = Column(Integer, nullable=True)
    reviewer = Column(String(255), nullable=True)
    note = Column(String(500), nullable=True)
    locked = Column(Integer, nullable=False, default=1)  # 人工锁定
    #: 决定修订号（CAS：乐观并发 —— 审阅冲突不覆盖他人决定）
    revision = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("action IN ('accept','reject')",
                        name="ck_data_fusion_decision_action"),
        CheckConstraint("revision >= 1",
                        name="ck_data_fusion_decision_revision"),
        CheckConstraint("geometry_side IS NULL OR geometry_side IN ('a','b')",
                        name="ck_data_fusion_decision_gside"),
        UniqueConstraint("plan_id", "pair_key",
                         name="uq_data_fusion_decision_pair"),
        Index("idx_data_fusion_decisions_plan", "plan_id", "created_at"),
    )
