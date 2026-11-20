"""Topology Workbench（G31）—— 持久实体（migration 0105）。

四张表（伴随实体，不另造任务状态机 / 内容存储）：

- ``topology_rulesets``  —— 规则集（版本化：改内容即 version+1 + 新 digest）；
- ``topology_runs``      —— 一次运行（durable job 关联 + 逐规则结果有界投影 +
  源绑定版本证据）；finding 大载荷走 witness blob，不入行；
- ``topology_findings``  —— 逐问题实体（稳定 finding_key、locator、度量、
  处理状态、witness 指针 + bbox）；(run_id, finding_key) 唯一；
- ``topology_exceptions`` —— accepted exception（理由/锚点/操作者；stale 判定）。

纪律（与 db_model.py / data_quality.py 全仓一致）：
- String PK + uuid4 默认；org_id 为 B 线占位列（只落列不启用）；
- CHECK 词表与迁移 0105 的 DDL 一字不差（wiring drift 闸依赖）；
- 行内 JSON 一律**有界投影**（locator ≤ 角色×要素数上限、metric 截断），
  完整几何证据走 BlobStore 内容寻址（witness_key + witness_sha256）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
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


class TopologyRuleset(Base):
    """规则集（多数据集业务拓扑约束的作者面）。"""

    __tablename__ = "topology_rulesets"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位
    project_id = Column(String(255), nullable=True)
    session_id = Column(String(255), nullable=True)
    name = Column(String(200), nullable=False)
    #: 版本：创建 = 1；每次规则内容修改 +1（历史运行持有自己的 digest）
    current_version = Column(Integer, nullable=False, default=1)
    #: 规范化规则数组（有界：≤32 条）
    rules = Column(JSON, nullable=False, default=list)
    #: 当前版本内容 digest（运行复用键成分之一）
    digest = Column(String(64), nullable=False, default="")
    created_by = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_topology_ruleset_version"),
        CheckConstraint("length(name) > 0", name="ck_topology_ruleset_name"),
        Index("idx_topology_ruleset_project", "project_id", "created_at"),
        Index("idx_topology_ruleset_session", "session_id", "created_at"),
    )


class TopologyRun(Base):
    """一次拓扑检查运行（作业层状态与逐规则质量结论分离）。"""

    __tablename__ = "topology_runs"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位
    ruleset_id = Column(
        String(36), ForeignKey("topology_rulesets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 运行时的规则集版本快照（与 ruleset 行解耦 —— 历史可读）
    ruleset_digest = Column(String(64), nullable=False, default="")
    ruleset_version = Column(Integer, nullable=False, default=1)
    rules_snapshot = Column(JSON, nullable=True)  # 运行时规则（有界投影）

    #: pending → running → completed / failed / cancelled
    status = Column(String(20), nullable=False, default="pending")
    #: 源绑定证据：角色 → {kind, ref/ids, revision, fingerprint, crs,
    #: feature_count, completeness, scope}（快照身份，复用键成分）
    bindings = Column(JSON, nullable=True)
    #: 全部源内容指纹 + 规则 digest + 计算 CRS + 方法版本的复合摘要
    input_digest = Column(String(64), nullable=False, default="")
    computation_crs = Column(String(64), nullable=True)
    method_version = Column(String(32), nullable=False, default="")

    #: 逐规则结果（有界：每规则一条，维度分离，任务书 §六）
    rule_results = Column(JSON, nullable=True)
    #: 运行级诚实性：{all_skipped: bool, budget_truncated: bool, notes: [...]}
    completeness = Column(JSON, nullable=True)
    findings_total = Column(Integer, nullable=False, default=0)
    findings_open = Column(Integer, nullable=False, default=0)
    #: witness 集合的 BlobStore 内容寻址指针（缺失 → 证据不可读，如实披露）
    witness_key = Column(String(64), nullable=True)
    witness_sha256 = Column(String(64), nullable=True)
    witness_feature_count = Column(Integer, nullable=False, default=0)

    job_id = Column(String(64), nullable=True)
    #: recheck 溯源：本运行如果是复验，指向被复验的运行
    recheck_of_run_id = Column(String(36), nullable=True)
    project_id = Column(String(255), nullable=True)
    session_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_topology_run_status",
        ),
        Index("idx_topology_run_ruleset", "ruleset_id", "created_at"),
        Index("idx_topology_run_project", "project_id", "created_at"),
        Index("idx_topology_run_session", "session_id", "created_at"),
        Index("idx_topology_run_input", "input_digest", "ruleset_digest"),
    )


class TopologyFinding(Base):
    """单条拓扑问题（稳定身份 + witness 定位 + 处理状态）。"""

    __tablename__ = "topology_findings"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    run_id = Column(
        String(36), ForeignKey("topology_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id = Column(String(64), nullable=False)
    rule_type = Column(String(48), nullable=False)
    #: 稳定身份：规则 digest + 快照内自然身份 + witness 分量 digest
    finding_key = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False, default="warn")
    #: 处理状态（检测事实不变）：open / accepted_exception / resolved / dismissed
    status = Column(String(24), nullable=False, default="open")

    #: 度量事实：{value, threshold, unit, comparison, method}（有界）
    metric = Column(JSON, nullable=True)
    #: 相关对象定位：[{role, kind, ref, content_revision, fingerprint,
    #:   identity{kind,value,snapshot_fingerprint}, feature_index}]（≤ 角色上限）
    locators = Column(JSON, nullable=True)
    #: witness 分量在运行 witness 集合中的索引 + 内容摘要
    witness_index = Column(Integer, nullable=False, default=0)
    witness_digest = Column(String(64), nullable=False, default="")
    #: 定位用 bbox（w,s,e,n；度）+ 几何类型（Polygon/LineString/…）
    bbox_w = Column(Float, nullable=False, default=0.0)
    bbox_s = Column(Float, nullable=False, default=0.0)
    bbox_e = Column(Float, nullable=False, default=0.0)
    bbox_n = Column(Float, nullable=False, default=0.0)
    geometry_type = Column(String(24), nullable=False, default="Polygon")
    #: 人类可读摘要（有界，不含坐标流）
    message = Column(String(500), nullable=False, default="")

    #: 复验溯源：本 finding 由哪次复验判定 resolved（证据链）
    resolved_by_run_id = Column(String(36), nullable=True)
    #: 例外关联（active 时指向例外记录）
    exception_id = Column(String(36), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('info','warn','error')",
            name="ck_topology_finding_severity",
        ),
        CheckConstraint(
            "status IN ('open','accepted_exception','resolved','dismissed')",
            name="ck_topology_finding_status",
        ),
        UniqueConstraint("run_id", "finding_key", name="uq_topology_finding_run_key"),
        Index("idx_topology_finding_run", "run_id", "severity", "status"),
        Index("idx_topology_finding_key", "finding_key"),
    )


class TopologyException(Base):
    """合法例外（用户决定；模型输出不得充当 —— WP09）。"""

    __tablename__ = "topology_exceptions"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    org_id = Column(Integer, nullable=True)  # B 线占位
    ruleset_id = Column(
        String(36), ForeignKey("topology_rulesets.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id = Column(String(64), nullable=False)
    #: 例外锚点：finding_key + 源内容指纹/revision + locator 身份 + witness_digest
    #: （重验锚点：源或规则前进 → stale，绝不按 bbox/文本相似自动延续）
    finding_key = Column(String(64), nullable=False)
    anchor = Column(JSON, nullable=True)
    #: 用户理由（必填，1..2000）
    reason = Column(String(2000), nullable=False)
    #: active / revoked / stale
    status = Column(String(16), nullable=False, default="active")
    created_by = Column(String(255), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','revoked','stale')",
            name="ck_topology_exception_status",
        ),
        CheckConstraint("length(reason) > 0", name="ck_topology_exception_reason"),
        Index("idx_topology_exception_ruleset", "ruleset_id", "status"),
        Index("idx_topology_exception_key", "finding_key", "status"),
    )


__all__ = [
    "TopologyRuleset", "TopologyRun", "TopologyFinding", "TopologyException",
]
