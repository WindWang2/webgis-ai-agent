"""Coordinate Operations（G45）—— 持久实体（migration g45_coordinate_operations）。

两张表（伴随实体；执行活性/取消/fencing 全部走既有 analysis_tasks 事实源，
不另造任务状态机；内容走 DATA_DIR/coord-ops + DataObject CAS）：

- ``coordinate_operation_plans`` —— 版本化坐标操作计划（作者态；revision
  CAS；锁定候选后冻结操作定义 + 环境证据；任何编辑 → 新 revision → 预览失效）；
- ``coordinate_operation_runs``  —— 一次全量执行（durable job 关联 + 有界
  收据 + 输出指针；幂等键锚 owner/源指纹/plan digest/operation digest/输出政策）。

纪律（与 data_join_authoring.py 一致）：String PK + uuid4；CHECK 词表与迁移
DDL 一字不差（drift 闸依赖）；行内 JSON 只存有界投影。
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
    Text,
)
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class CoordinateOperationPlan(Base):
    """坐标操作计划（草稿保存不执行、不改源、不改 MapSpec）。

    锁定语义：``selected_candidate_id`` 非空 + ``operation``（冻结 PROJJSON +
    digest + 环境）非空 —— 由服务端 lock 路径原子落盘；计划内容任何变化 →
    ``revision`` +1 且服务层清空预览绑定（预览按 digest 失效）。
    """

    __tablename__ = "coordinate_operation_plans"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)
    session_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)
    title = Column(String(200), nullable=False, default="")
    #: draft / locked / archived
    status = Column(String(20), nullable=False, default="draft")
    #: 计划修订号：创建 = 1；每次内容修改 +1（CAS 令牌）
    revision = Column(Integer, nullable=False, default=1)
    #: 计划 JSON（源/目标 CRS canonical 描述、AOI、维度/失败政策、预算覆盖）
    plan = Column(JSON, nullable=False)
    #: 规范化 digest（预览/执行/幂等键锚点；含源指纹与输出政策）
    normalized_digest = Column(String(64), nullable=False, default="")
    #: 源指纹（describe 时更新；执行前复核锚点）
    source_fingerprint = Column(String(128), nullable=True)
    #: 服务器签发候选 id（lock 时落）
    selected_candidate_id = Column(String(64), nullable=True)
    #: 冻结操作（PROJJSON + definition_sha256 + environment + candidate 摘要）
    operation = Column(Text, nullable=True)
    #: 最新预览证据（有界；样本规则/发现/坐标对摘要）
    preview = Column(JSON, nullable=True)
    #: 预览针对的 digest（计划/源变化 → 失效）
    preview_digest = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_coord_op_plan_revision"),
        CheckConstraint(
            "status IN ('draft','locked','archived')",
            name="ck_coord_op_plan_status"),
        Index("idx_coord_op_plans_session", "session_id", "created_at"),
    )


class CoordinateOperationRun(Base):
    """一次全量执行（作业层状态与收据分离；内容指针可回读才 completed）。"""

    __tablename__ = "coordinate_operation_runs"

    id = Column(String(40), primary_key=True)
    plan_id = Column(
        String(36), ForeignKey("coordinate_operation_plans.id", ondelete="CASCADE"),
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
    idempotency_key = Column(String(255), nullable=True)
    #: 有界收据（行/顶点/操作 manifest/环境/成果指针/显示副本/预算实测）
    receipt = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    #: 权威成果（ref:coord-ops/<run_id>；fabric ref 与 display ref 在收据内）
    output_ref = Column(String(255), nullable=True)
    output_sha256 = Column(String(128), nullable=True)
    output_rows = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_coord_op_run_status"),
        CheckConstraint("plan_revision >= 1", name="ck_coord_op_run_plan_rev"),
        Index("idx_coord_op_runs_idem", "idempotency_key", "created_at"),
        Index("idx_coord_op_runs_session", "session_id", "created_at"),
        Index("idx_coord_op_runs_plan", "plan_id", "created_at"),
    )
