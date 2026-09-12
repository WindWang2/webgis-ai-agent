"""Data Lifecycle V9 —— 统一生命周期策略引擎实体（migration 0047，ADR-0140）。

P3/P5 的落库事实面：

- ``lifecycle_object``  —— 五类对象（lakehouse dataset / fabric 物化 /
  artifact / COG 输出 / worker_cache）的**统一登记视图**：适配器只读枚举
  各机制现状后 upsert 到此（可重建投影，非事实源）；
- ``lifecycle_policy``  —— 分级/动作策略（hot/warm/cold → observe /
  stage_delete / delete）。**默认策略 = 行为等价现状（全 observe）**，
  任何删除动作必须运营显式启用 —— 这是 ADR-0140 的安全不动点；
- ``gc_plan``           —— 回收计划审批状态机（draft → pending_approval →
  approved → executing → done / failed / rolled_back / rejected / cancelled），
  P5 闭环的状态事实源。

约定：org_id 为 B 线占位列（nullable 无 FK）；CHECK 词表与模型一字不差；
JSON 列全部有界投影。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
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


#: 五类对象词表（封闭；新增必须同步 adapters/adapters.py）。
LIFECYCLE_OBJECT_KINDS = (
    "lakehouse_dataset",
    "fabric_materialization",
    "artifact_cache",
    "cog_output",
    "worker_cache",
)

#: 动作词表。observe = 只登记观测（现状等价）；stage_delete = P5 staging
#: 二段式；delete = 直删（仅明确无 staging 语义的对象类可用）。
LIFECYCLE_ACTIONS = ("observe", "stage_delete", "delete")

#: GC 计划状态机词表（P5）。
GC_PLAN_STATES = (
    "draft", "pending_approval", "approved", "executing",
    "done", "failed", "rolled_back", "rejected", "cancelled",
)


class LifecycleObject(Base):
    """统一登记视图（适配器枚举 → upsert；可由各机制事实源重建）。"""

    __tablename__ = "lifecycle_objects"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位列
    kind = Column(String(32), nullable=False)
    #: 机制内自然键（如 dataset id / spill 相对路径 / sha256 / worker:cache_key）
    object_id = Column(String(255), nullable=False)
    owner_scope = Column(String(128), nullable=False, default="")
    byte_size = Column(BigInteger().with_variant(Integer, "sqlite"),
                       nullable=False, default=0)
    tier = Column(String(8), nullable=False, default="hot")
    last_used_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    info = Column(JSON, nullable=True)  # 有界机制侧元数据（≤16 键）

    __table_args__ = (
        UniqueConstraint("kind", "object_id", name="uq_lifecycle_object_natural"),
        CheckConstraint(
            "kind IN ('lakehouse_dataset','fabric_materialization',"
            "'artifact_cache','cog_output','worker_cache')",
            name="ck_lifecycle_object_kind",
        ),
        CheckConstraint("tier IN ('hot','warm','cold')",
                        name="ck_lifecycle_object_tier"),
        Index("idx_lifecycle_object_kind_tier", "kind", "tier"),
        Index("idx_lifecycle_object_last_used", "kind", "last_used_at"),
    )


class LifecyclePolicy(Base):
    """分级/动作策略（默认种子由 PolicyEngine.ensure_defaults 写入）。"""

    __tablename__ = "lifecycle_policies"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位列
    #: 唯一策略名（如 "default" / "cog-strict"）；同 (kind) 命名即适用
    name = Column(String(64), nullable=False, unique=True)
    kind = Column(String(32), nullable=False)
    action = Column(String(16), nullable=False, default="observe")
    #: tier 阈值（秒）：{"warm_after_s": 86400, "cold_after_s": 604800}
    tier_thresholds = Column(JSON, nullable=True)
    #: stage_delete 的 staging 观察期（小时；0 = 无观察期即删）
    staging_hours = Column(Integer, nullable=False, default=72)
    enabled = Column(Boolean, nullable=False, default=False)
    params = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('lakehouse_dataset','fabric_materialization',"
            "'artifact_cache','cog_output','worker_cache')",
            name="ck_lifecycle_policy_kind",
        ),
        CheckConstraint("action IN ('observe','stage_delete','delete')",
                        name="ck_lifecycle_policy_action"),
        Index("idx_lifecycle_policy_kind", "kind", "enabled"),
    )


class GcPlan(Base):
    """回收计划（P5 审批状态机的事实源；dry-run 树有界落库）。"""

    __tablename__ = "gc_plans"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位列
    created_by = Column(String(255), nullable=True)
    approved_by = Column(String(255), nullable=True)
    executed_by = Column(String(255), nullable=True)

    status = Column(String(20), nullable=False, default="draft")
    #: 计划范围（kinds × tiers 过滤的规范投影）
    scope = Column(JSON, nullable=True)
    #: dry-run 结果树（对象 → 依赖 → 回收原因 → 预估释放量；有界）
    plan_tree = Column(JSON, nullable=True)
    candidate_count = Column(Integer, nullable=False, default=0)
    candidate_bytes = Column(BigInteger().with_variant(Integer, "sqlite"),
                             nullable=False, default=0)
    #: staging 观察期到点（二段式物理删闸门）
    staging_expires_at = Column(DateTime, nullable=True)
    result = Column(JSON, nullable=True)
    #: durable job 关联（执行体）
    job_id = Column(String(64), nullable=True)
    #: 幂等：同 scope 同树的重放复用同一计划
    plan_digest = Column(String(64), nullable=False, default="")

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','pending_approval','approved','executing',"
            "'done','failed','rolled_back','rejected','cancelled')",
            name="ck_gc_plan_status",
        ),
        Index("idx_gc_plan_status", "status", "created_at"),
        Index("idx_gc_plan_digest", "plan_digest"),
    )


__all__ = [
    "LifecycleObject",
    "LifecyclePolicy",
    "GcPlan",
    "LIFECYCLE_OBJECT_KINDS",
    "LIFECYCLE_ACTIONS",
    "GC_PLAN_STATES",
]
