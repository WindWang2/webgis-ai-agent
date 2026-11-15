"""Data Join Authoring（G38）—— 持久实体（migration 0107）。

两张表（伴随实体；执行活性/取消/fencing 全部走既有 analysis_tasks 事实源，
不另造任务状态机；内容走 DATA_DIR/data-join + DataObject CAS，不在行内
存可写结果副本）：

- ``data_join_plans`` —— 版本化关联计划（作者态；revision CAS 修改）；
- ``data_join_runs``  —— 一次执行（durable job 关联 + 有界收据 + 输出指针）。

纪律（与 topology_workbench.py 一致）：String PK + uuid4；CHECK 词表与
迁移 DDL 一字不差（drift 闸依赖）；行内 JSON 只存有界投影（诊断样本、
收据摘要），完整输出在终态包文件 + BlobStore/DataObject。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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


class DataJoinPlan(Base):
    """关联计划（草稿保存不执行、不改源、不改 MapSpec）。"""

    __tablename__ = "data_join_plans"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False, default="")
    #: draft / archived（archived = 作者显式归档，不删除历史 run）
    status = Column(String(20), nullable=False, default="draft")
    #: 计划修订号：创建 = 1；每次内容修改 +1（CAS 令牌）
    revision = Column(Integer, nullable=False, default=1)
    #: 计划 JSON（JoinPlan.model_dump；有界 ≤32KB 由服务层保证）
    plan = Column(JSON, nullable=False)
    #: 规范化 digest（预览/执行/幂等键锚点）
    normalized_digest = Column(String(64), nullable=False, default="")
    #: 双源指纹（执行幂等键成分；预览时更新）
    left_fingerprint = Column(String(128), nullable=True)
    right_fingerprint = Column(String(128), nullable=True)
    #: 最新预览证据（有界 ≤8KB：计数/basis/样本数；服务层保证）
    preview = Column(JSON, nullable=True)
    #: 预览针对的 digest（计划或源变化 → 失效）
    preview_digest = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_data_join_plan_revision"),
        CheckConstraint(
            "status IN ('draft','archived')", name="ck_data_join_plan_status"),
        Index("idx_data_join_plans_session", "session_id", "created_at"),
    )


class DataJoinRun(Base):
    """一次关联执行（作业层状态与收据分离；内容指针可回读才 completed）。"""

    __tablename__ = "data_join_runs"

    id = Column(String(40), primary_key=True)
    plan_id = Column(
        String(36), ForeignKey("data_join_plans.id", ondelete="CASCADE"),
        nullable=False)
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    #: 执行时的计划修订与 digest（重放对账锚点）
    plan_revision = Column(Integer, nullable=False)
    plan_digest = Column(String(64), nullable=False)
    #: pending / running / completed / failed / cancelled
    status = Column(String(20), nullable=False, default="pending")
    job_id = Column(Integer, nullable=True)
    #: 幂等键（与 durable job 同键；并发收敛锚点）
    idempotency_key = Column(String(255), nullable=True)
    #: 有界收据（完整行数、匹配基数、输入版本、输出指针、DataObject、
    #: 渲染投影、诊断摘要、错误码）
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    output_ref = Column(String(255), nullable=True)         # ref:data-join/<run>
    output_sha256 = Column(String(128), nullable=True)
    output_rows = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_data_join_run_status"),
        CheckConstraint("plan_revision >= 1", name="ck_data_join_run_plan_rev"),
        # 幂等收敛在 service 层（活跃 run 复用 + 最新 run 收据短路）；
        # 历史可有多行同键（失效后重执行），故只建索引不建唯一约束。
        Index("idx_data_join_runs_idem", "idempotency_key", "created_at"),
        Index("idx_data_join_runs_session", "session_id", "created_at"),
        Index("idx_data_join_runs_plan", "plan_id", "created_at"),
    )
