"""Cartography Intent Learning: carto_intent_evidence + carto_feedback_signals +
carto_recipe_affinity（+ carto_project_facts 增列 confidence/expires_at）

Revision ID: 0057_cartography_intent_learning
Revises: 0056_cartography_quality_facts
Create Date: 2026-09-13

任务书 adaptive-cartography/v11 W1（ADR-0161）：「能记住、会改过」的学习基座
（缺口 G11）。

- ``carto_intent_evidence``   —— 意图裁决证据库（可查询/回放；W1.1）；
- ``carto_feedback_signals``  —— 反馈信号账本（{type, weight, decay}；W1.2）；
- ``carto_recipe_affinity``   —— 配方亲和（fallback 链学习；W1.3）；
- ``carto_project_facts``     —— **只增列**：confidence（记忆项置信度）+
  expires_at（过期策略），W1.4（既有字段零改动，repo 纪律）。

- additive 新表 + 增列（create_all-coexistence guard 可重入 DDL）；
  downgrade 反序回滚，先删增列再删表；
- 领号 0057（migrations/.alloc.json，adaptive-cartography 段 0056–0065）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0057_cartography_intent_learning"
down_revision: Union[str, Sequence[str], None] = "0056_cartography_quality_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVIDENCE = "carto_intent_evidence"
_SIGNALS = "carto_feedback_signals"
_AFFINITY = "carto_recipe_affinity"
_FACTS = "carto_project_facts"


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND :n = name"),
            {"n": name},
        ).fetchall()
        return bool(rows)
    try:
        rows = bind.execute(
            sa.text("SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = :n AND table_schema = current_schema()"),
            {"n": name},
        ).fetchall()
    except Exception:  # noqa: BLE001 —— 非 information_schema 方言（保守跳过）
        return False
    return bool(rows)


def _columns_of(name: str) -> set:
    """列名集合（双后端：sqlite PRAGMA / postgres information_schema）。

    （迁移门禁修复：按方言分流 —— 非 sqlite 方言先打 PRAGMA 会以语法
    错误中止事务，后续语句全部 InFailedSqlTransaction。）
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        try:
            rows = bind.execute(sa.text(f"PRAGMA table_info({name})")).fetchall()
            if rows:
                return {r[1] for r in rows}
        except Exception:  # noqa: BLE001 —— PRAGMA 不可用则退 information_schema
            pass
    try:
        rows = bind.execute(
            sa.text("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :n AND table_schema = current_schema()"),
            {"n": name},
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def upgrade() -> None:
    if not _table_exists(_EVIDENCE):
        op.create_table(
            _EVIDENCE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("lang", sa.String(length=8), nullable=True),
            sa.Column("query_text", sa.String(length=512), nullable=True),
            sa.Column("query_hash", sa.String(length=64), nullable=True),
            sa.Column("task", sa.String(length=64), nullable=True),
            sa.Column("fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("matched_rules", sa.JSON(), nullable=False),
            sa.Column("task_candidates", sa.JSON(), nullable=False),
            sa.Column("slots", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("confidence_components", sa.JSON(), nullable=False),
            sa.Column("clarification", sa.JSON(), nullable=False),
            sa.Column("degraded_reason", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="rule"),
            sa.Column("app_version", sa.String(length=32), nullable=True),
            sa.CheckConstraint("source IN ('rule','llm')", name="ck_intent_evidence_source"),
        )
        op.create_index("ix_carto_intent_evidence_session_id", _EVIDENCE, ["session_id"])
        op.create_index("ix_carto_intent_evidence_query_hash", _EVIDENCE, ["query_hash"])
        op.create_index("idx_intent_evidence_created", _EVIDENCE, ["created_at"])
        op.create_index("idx_intent_evidence_task", _EVIDENCE, ["task"])

    if not _table_exists(_SIGNALS):
        op.create_table(
            _SIGNALS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("signal_type", sa.String(length=32), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("decay_days", sa.Float(), nullable=False, server_default="30"),
            sa.Column("target", sa.String(length=128), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.CheckConstraint("weight > 0", name="ck_feedback_weight_positive"),
        )
        op.create_index("ix_carto_feedback_signals_session_id", _SIGNALS, ["session_id"])
        op.create_index("ix_carto_feedback_signals_project_id", _SIGNALS, ["project_id"])
        op.create_index("idx_feedback_target_created", _SIGNALS, ["target", "created_at"])

    if not _table_exists(_AFFINITY):
        op.create_table(
            _AFFINITY,
            sa.Column("recipe_id", sa.String(length=128), primary_key=True),
            sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cold_start_weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("last_outcome", sa.String(length=16), nullable=True),
            sa.Column("last_updated", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "last_outcome IS NULL OR last_outcome IN ('success','failure')",
                name="ck_recipe_affinity_outcome",
            ),
        )

    cols = _columns_of(_FACTS)
    if cols and "confidence" not in cols:
        op.add_column(_FACTS, sa.Column("confidence", sa.Float(), nullable=True))
    if cols and "expires_at" not in cols:
        op.add_column(_FACTS, sa.Column("expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    cols = _columns_of(_FACTS)
    if cols and "expires_at" in cols:
        op.drop_column(_FACTS, "expires_at")
    if cols and "confidence" in cols:
        op.drop_column(_FACTS, "confidence")
    if _table_exists(_AFFINITY):
        op.drop_table(_AFFINITY)
    if _table_exists(_SIGNALS):
        op.drop_index("idx_feedback_target_created", table_name=_SIGNALS)
        op.drop_index("ix_carto_feedback_signals_project_id", table_name=_SIGNALS)
        op.drop_index("ix_carto_feedback_signals_session_id", table_name=_SIGNALS)
        op.drop_table(_SIGNALS)
    if _table_exists(_EVIDENCE):
        op.drop_index("idx_intent_evidence_task", table_name=_EVIDENCE)
        op.drop_index("idx_intent_evidence_created", table_name=_EVIDENCE)
        op.drop_index("ix_carto_intent_evidence_query_hash", table_name=_EVIDENCE)
        op.drop_index("ix_carto_intent_evidence_session_id", table_name=_EVIDENCE)
        op.drop_table(_EVIDENCE)
