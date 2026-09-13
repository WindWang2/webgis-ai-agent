"""Intent Learning Models（V11 W1，ADR-0161）—— 意图证据 / 反馈信号 / 配方亲和。

三张表构成「能记住、会改过」的学习基座（缺口 G11）：

- ``carto_intent_evidence`` —— 意图裁决证据库：每次语义裁决（slots / task
  决策 / 置信度分量 / 澄清请求）落一行，可查询、可回放；是澄清命中率与
  误触发率台账的数据源，也是 W1.6 报表面。
- ``carto_feedback_signals`` —— 反馈信号账本：用户后续行为（换色带/换图型/
  重发问/接受）按 ``feedback_signal{type, weight, decay}`` 记账；权重读取时
  按半衰期衰减。信号不直接改行为 —— 只被 :mod:`recipe_affinity` 与记忆
  聚合消费（先验而非证据，与 ADR-0069 决策 2 同纪律）。
- ``carto_recipe_affinity`` —— 配方/回退链亲和：recipe_id 唯一一行，成功/
  失败计数 + 学习权重；``resolve_fallback_chain`` 只把它用作**同分并列时的
  排序先验**（冷启动默认 0 = 中性，确定性 tie-break 保留），不允许它推翻
  规则裁决。

隐私边界：``carto_intent_evidence`` 存查询文本但截断（512 字符）且仅用于
本仓评测/回放；不外发（W7 VLM 脱敏开关独立审计）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Column,
)

from app.models.project import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CartoIntentEvidence(Base):
    """一行 = 一次意图语义裁决的完整证据（可回放）。"""

    __tablename__ = "carto_intent_evidence"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    session_id = Column(String(255), nullable=True, index=True)
    lang = Column(String(8), nullable=True)
    # 查询文本截断 512 —— 回放足够，无界载荷不落库
    query_text = Column(String(512), nullable=True)
    query_hash = Column(String(64), nullable=True, index=True)
    # 裁决结果（与 intent_semantic 输出同构的 JSON 快照）
    task = Column(String(64), nullable=True)
    fallback = Column(Boolean, nullable=False, default=False)
    matched_rules = Column(JSON, nullable=False, default=list)
    task_candidates = Column(JSON, nullable=False, default=list)
    slots = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=True)
    confidence_components = Column(JSON, nullable=False, default=dict)
    clarification = Column(JSON, nullable=False, default=dict)
    degraded_reason = Column(String(64), nullable=True)
    source = Column(String(16), nullable=False, default="rule")  # rule | llm
    app_version = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source IN ('rule','llm')", name="ck_intent_evidence_source"
        ),
        Index("idx_intent_evidence_created", "created_at"),
        Index("idx_intent_evidence_task", "task"),
    )


class CartoFeedbackSignal(Base):
    """一行 = 一次用户反馈行为（先验账本；读取时按 decay 半衰期衰减）。"""

    __tablename__ = "carto_feedback_signals"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    session_id = Column(String(255), nullable=True, index=True)
    project_id = Column(String(255), nullable=True, index=True)
    # 色带更换 | 图型更换 | 重发问 | 接受 | 拒绝 | 修复成功 | 修复失败 …
    signal_type = Column(String(32), nullable=False)
    weight = Column(Float, nullable=False, default=1.0)
    # 半衰期（天）；<=0 视为不衰减
    decay_days = Column(Float, nullable=False, default=30.0)
    # 信号指向的对象：recipe:<id> / palette:<name> / slot:<name> / session
    target = Column(String(128), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_feedback_target_created", "target", "created_at"),
        CheckConstraint("weight > 0", name="ck_feedback_weight_positive"),
    )


class CartoRecipeAffinity(Base):
    """一行 = 一个配方的学习亲和（成功率先验；规则裁决的 tie-break 输入）。"""

    __tablename__ = "carto_recipe_affinity"

    recipe_id = Column(String(128), primary_key=True)
    success_count = Column(Integer, nullable=False, default=0)
    failure_count = Column(Integer, nullable=False, default=0)
    # 学习权重：logistic((success - failure) 先验)，冷启动 0.0（中性）
    weight = Column(Float, nullable=False, default=0.0)
    # 冷启动默认权重（新配方首评前使用；W1.3 语义）
    cold_start_weight = Column(Float, nullable=False, default=0.0)
    last_outcome = Column(String(16), nullable=True)  # success | failure
    last_updated = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN ('success','failure')",
            name="ck_recipe_affinity_outcome",
        ),
    )


__all__ = [
    "CartoIntentEvidence",
    "CartoFeedbackSignal",
    "CartoRecipeAffinity",
]
