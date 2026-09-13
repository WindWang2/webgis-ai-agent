"""Cartography Quality Facts —— 制图质量事实库（migration 0056，ADR-0159）。

任务书 adaptive-cartography/10 P1：``_cartographic_review`` 与
``HarnessEvaluator`` 产出的质量证据此前只活在会话状态与进程内存里
（cartographic-closed-loop.md："no database or migration is introduced"），
无法画跨版本质量曲线，也无法设 ratchet（只许变好）闸。本模块把**事实**
（一次评审 run + 逐检查项观测值）落库，判定逻辑不变——这里只是产出面的
下游账本。

约定（与 data_quality.py / 0046 全仓纪律一致）：

- run 是领域对象 → String PK + uuid4 默认；逐检查行走高吞吐
  → BigInteger sqlite 变体自增（analysis_tasks / quality_rule_results 同款）；
- CHECK 词表与迁移 0056 的 DDL 一字不差（wiring drift 闸
  tests/test_deploy_migration_wiring.py::test_migrated_schema_matches_models
  依赖模型与迁移产物一致）；
- ``summary``/``evidence`` 只装有界投影——大载荷永不入行（规范 §38 纪律，
  闭环评审的 fingerprint/bounded summary 同款）；
- 本表只增不改既有表，downgrade 反序回滚，不破坏既有数据。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_hex() -> str:
    return uuid.uuid4().hex


class CartographyQualityRun(Base):
    """一次制图质量评审的 run 实体（desired_state / runtime / eval / adaptive / golden）。

    ``lane`` 描述证据产出的阶段，与 cartographic-closed-loop.md 的两级评审对齐：
    desired_state（生命周期提交面）与 runtime（harness 实际态）之外，eval 是
    离线评估套件（HarnessEvaluator 消费面），adaptive 是 P5 同需求多轮验收集，
    golden 是 P3 头照 golden diff 断言。
    """

    __tablename__ = "cartography_quality_runs"

    id = Column(String(36), primary_key=True, default=_uuid_hex)
    ts = Column(DateTime, default=_utcnow, nullable=False)
    #: 产出进程的版本（git describe / VERSION），跨版本趋势对比的横轴
    app_version = Column(String(64), nullable=False, default="")
    session_id = Column(String(255), nullable=True)
    #: 场景/图型标识（头照场景名、golden 场景名、语料 scene_id……可空）
    scene_id = Column(String(128), nullable=True)
    lane = Column(String(20), nullable=False, default="runtime")
    #: 产出处标识（runtime_review / harness_evaluator / desired_review / …）
    source = Column(String(64), nullable=False, default="")
    #: 该 run 的总体判定（无整体判定的纯观测 run 为 NULL）
    passed = Column(Boolean, nullable=True)
    #: 有界摘要投影（状态词、终止原因、计数），不含检查项明细 —— 明细在 metrics
    summary = Column(JSON, nullable=True)

    metrics = relationship(
        "CartographyQualityMetric",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "lane IN ('desired_state','runtime','eval','adaptive','golden')",
            name="ck_carto_quality_run_lane",
        ),
        Index("idx_carto_quality_run_ts", "ts"),
        Index("idx_carto_quality_run_scene_ts", "scene_id", "ts"),
        Index("idx_carto_quality_run_lane_ts", "lane", "ts"),
    )


class CartographyQualityBaseline(Base):
    """「图型 × 检查项」ratchet 基线（P2）：取分位数而非均值，抗离群。

    ``status='provisional'`` 的首轮基线只记录不拦截；转 ``active`` 后，
    新观测劣于基线（超容差）即被 ratchet 闸拦截。
    """

    __tablename__ = "cartography_quality_baselines"

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    #: 图型/场景 scope；"*" 为全局兜底（先查 scene，再回退 *）
    scene_id = Column(String(128), nullable=False, default="*")
    check_id = Column(String(128), nullable=False)
    #: 基准值（观测分位数，方向语义见 direction）
    value = Column(Float, nullable=False)
    #: 基准值取的分位（0-1，默认 p66）
    quantile = Column(Float, nullable=False, default=0.66)
    #: high_bad：越大越糟；low_bad：越小越糟
    direction = Column(String(8), nullable=False, default="high_bad")
    #: 劣化容差百分比（默认 ±5）
    tolerance_pct = Column(Float, nullable=False, default=5.0)
    #: provisional（只记录） / active（拦截）
    status = Column(String(16), nullable=False, default="provisional")
    #: first_run / calibration / manual
    source = Column(String(24), nullable=False, default="first_run")
    sample_n = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('provisional','active')",
            name="ck_carto_quality_baseline_status",
        ),
        CheckConstraint(
            "direction IN ('high_bad','low_bad')",
            name="ck_carto_quality_baseline_direction",
        ),
        Index(
            "uq_carto_quality_baseline_scope",
            "scene_id",
            "check_id",
            unique=True,
        ),
    )


class CartographyQualityWaiver(Base):
    """ratchet 豁免（P2）：带理由 + 到期日，到期自动失效。"""

    __tablename__ = "cartography_quality_waivers"

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    scene_id = Column(String(128), nullable=False, default="*")
    check_id = Column(String(128), nullable=False)
    reason = Column(String(500), nullable=False)
    created_by = Column(String(128), nullable=False, default="")
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("idx_carto_quality_waiver_scope", "scene_id", "check_id"),
        Index("idx_carto_quality_waiver_expires", "expires_at"),
    )


class CartographyQualityMetric(Base):
    """run 内逐检查项的观测行：check_id × value × evidence_class × verdict。

    ``value`` 是证据里的数值观测（load_ratio / min_adjacent_delta_e /
    label_ink_ratio / avg_feature_area_px / encoded_field_count / gate 得分……）；
    证据缺失时为 NULL —— 缺观测 ≠ 0 分，与"无证据 ≠ 成功"的诚实语义一致。
    """

    __tablename__ = "cartography_quality_metrics"

    # 高吞吐行：sqlite rowid 自增变体（quality_rule_results 同款）。
    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    run_id = Column(
        String(36),
        ForeignKey("cartography_quality_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 检查项 id（carto.load.ratio / ToolChoiceAccuracy / golden:heatmap-basic …）
    check_id = Column(String(128), nullable=False)
    #: 数值观测；证据缺失（not_evaluated）时为 NULL
    value = Column(Float, nullable=True)
    evidence_class = Column(String(16), nullable=False, default="deterministic")
    #: pass / fail / warning / not_evaluated
    verdict = Column(String(16), nullable=False, default="not_evaluated")

    run = relationship("CartographyQualityRun", back_populates="metrics")

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('pass','fail','warning','not_evaluated')",
            name="ck_carto_quality_metric_verdict",
        ),
        Index("idx_carto_quality_metric_run", "run_id"),
        Index("idx_carto_quality_metric_check", "check_id"),
    )
