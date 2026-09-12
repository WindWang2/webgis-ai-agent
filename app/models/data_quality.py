"""Data Quality V9 —— QualityReport / QualityRuleResult 实体（migration 0046）。

P1（任务书 §2）：把 493 行薄壳 data_quality 升为子系统的**落库事实面**：

- ``quality_reports``       —— 一次数据质量评估的报告实体（同步小数据集与
  durable job 大数据集双路径共用同一实体）；
- ``quality_rule_results``  —— 报告内逐规则的判定行（规则耗时/命中事实，
  观测指标的落库对应物）。

约定（与 db_model.py 全仓纪律一致）：

- 领域表 String PK + uuid4 默认；结果行高吞吐 → BigInteger sqlite 变体自增；
- ``org_id`` 是 B 线（security-tenancy-v9）占位列：本线只落列、不启用
  （nullable、无 FK —— B 线合入后再补约束，避免双线改同一约束）；
- CHECK 词表与 status/state 枚举和迁移 0046 的 DDL 一字不差
  （列元组漂移守卫 tests/test_deploy_migration_wiring.py 依赖）；
- 报告 JSON 一律**有界投影**（≤64 条结果行 / metric 字典截断），大载荷
  走 result_ref 指针，绝不整份入行（规范 §38 同纪律）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class QualityReport(Base):
    """一次数据质量评估报告（双路径共用：同步 evaluate / durable job）。"""

    __tablename__ = "quality_reports"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    # B 线占位列：只落列不启用（§8 —— 查询侧兼容读取，列允许缺失）。
    org_id = Column(Integer, nullable=True)
    # project_id 不声明 index=True：迁移链建的是复合覆盖索引
    # idx_quality_report_project_created(project_id, created_at)
    # （0046），单列索引是冗余 —— 模型必须与迁移产物一致（wiring drift 闸）。
    project_id = Column(String(255), nullable=True)
    session_id = Column(String(255), nullable=True)
    created_by = Column(String(255), nullable=True)

    #: 被评对象：session ref / 文件路径 / 显式数据指纹（诚实缺省 ""）
    target_ref = Column(String(255), nullable=False, default="")
    #: vector / raster / table —— 决定规则适用子集
    target_kind = Column(String(16), nullable=False, default="vector")
    #: 输入内容指纹（同指纹同规则 ⇒ 可复用报告；画像/失效联动 P2）
    dataset_identity = Column(String(128), nullable=False, default="")
    #: DSL 指纹（规则集变更 ⇒ 历史报告不与新报告混比）
    ruleset_digest = Column(String(64), nullable=False, default="")

    #: 生命周期：pending → running → completed / failed
    status = Column(String(20), nullable=False, default="pending")
    #: 业务结论：pass / warn / fail
    overall_status = Column(String(16), nullable=False, default="pass")

    rule_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    warn_count = Column(Integer, nullable=False, default=0)
    skipped_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)

    #: 有界报告摘要（不含逐规则行 —— 那是 quality_rule_results）
    summary = Column(JSON, nullable=True)
    diagnostics = Column(JSON, nullable=True)
    #: durable job 关联（大路径回链任务中心）
    job_id = Column(String(64), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_quality_report_status",
        ),
        CheckConstraint(
            "overall_status IN ('pass','warn','fail')",
            name="ck_quality_report_overall",
        ),
        CheckConstraint(
            "target_kind IN ('vector','raster','table')",
            name="ck_quality_report_target_kind",
        ),
        Index("idx_quality_report_project_created", "project_id", "created_at"),
        Index("idx_quality_report_session_created", "session_id", "created_at"),
        Index("idx_quality_report_identity", "dataset_identity", "ruleset_digest"),
    )


class QualityRuleResult(Base):
    """报告内逐规则判定行（规则耗时/命中事实的落库形态）。"""

    __tablename__ = "quality_rule_results"

    # 高吞吐行：sqlite rowid 自增变体（analysis_tasks 同款）。
    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    report_id = Column(
        String(36),
        ForeignKey("quality_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id = Column(String(64), nullable=False)
    rule_type = Column(String(48), nullable=False)
    #: 规则自身严重级（DSL 声明）
    severity = Column(String(16), nullable=False, default="warn")
    #: 判定：pass / warn / fail / skipped / error
    status = Column(String(16), nullable=False, default="pass")
    message = Column(String(500), nullable=False, default="")
    affected_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    #: 有界实测事实（命中率/极值/样例键），绝不夹带原始载荷
    metric = Column(JSON, nullable=True)
    #: 修复建议管线：autofixable 子集带 REMEDIATION_OPS 操作词表
    autofixable = Column(Boolean, nullable=False, default=False)
    fix_operations = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pass','warn','fail','skipped','error')",
            name="ck_quality_rule_result_status",
        ),
        CheckConstraint(
            "severity IN ('info','warn','error')",
            name="ck_quality_rule_result_severity",
        ),
        Index("idx_quality_rule_result_report", "report_id"),
        Index("idx_quality_rule_result_rule", "rule_id", "status"),
    )


__all__ = ["QualityReport", "QualityRuleResult"]
