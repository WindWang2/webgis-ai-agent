"""G37: session model runtime (catalog prefs / turn receipts / usage records)

Revision ID: g3701_session_model
Revises: 0106_lifecycle_policy_org_unique
Create Date: 2026-11-14

三张纯新表（additive，无数据迁移）：
- ``chat_session_model_prefs``：会话级模型 preference（CAS revision，键 =
  既有 conversations.id，owner 校验在路由层）；
- ``chat_turn_runtime``：每 turn 模型运行回执 + 用量汇总（requested/
  effective/response_model 分列，completeness 诚实分层）；
- ``chat_usage_records``：每条原生 message_end 终态 usage（(turn_id,
  call_seq) 唯一 → 重放不重复计费）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g3701_session_model"
down_revision: Union[str, Sequence[str], None] = "0106_lifecycle_policy_org_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_session_model_prefs",
        sa.Column(
            "conversation_id", sa.String(255),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="inherit"),
        sa.Column("catalog_item_id", sa.String(160), nullable=True),
        sa.Column("thinking_level", sa.String(16), nullable=True),
        sa.Column("session_token_cap", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.CheckConstraint("mode IN ('inherit', 'explicit')", name="ck_csmp_mode"),
        sa.CheckConstraint("revision >= 1", name="ck_csmp_revision"),
    )

    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "chat_turn_runtime",
        sa.Column("id", bigint, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False, unique=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("runtime_epoch", sa.String(64), nullable=False, server_default=""),
        sa.Column("catalog_generation", sa.String(32), nullable=False, server_default=""),
        sa.Column("config_generation", sa.String(32), nullable=False, server_default=""),
        sa.Column("preference_revision", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="inherit"),
        sa.Column("requested_model", sa.String(160), nullable=False, server_default=""),
        sa.Column("thinking_requested", sa.String(16), nullable=True),
        sa.Column("effective_provider", sa.String(64), nullable=True),
        sa.Column("effective_model", sa.String(160), nullable=True),
        sa.Column("thinking_effective", sa.String(16), nullable=True),
        sa.Column("response_model", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("tokens_input", bigint, nullable=False, server_default="0"),
        sa.Column("tokens_output", bigint, nullable=False, server_default="0"),
        sa.Column("tokens_cache_read", bigint, nullable=False, server_default="0"),
        sa.Column("tokens_cache_write", bigint, nullable=False, server_default="0"),
        sa.Column("tokens_total", bigint, nullable=False, server_default="0"),
        sa.Column("cost_total", bigint, nullable=True),
        sa.Column("cost_source", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("records_expected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'inherit_default', 'degraded_inherit', "
            "'failed', 'budget_rejected')",
            name="ck_ctr_status"),
    )
    op.create_index("idx_ctr_session_created", "chat_turn_runtime", ["session_id", "created_at"])

    op.create_table(
        "chat_usage_records",
        sa.Column("id", bigint, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("call_seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("response_model", sa.String(160), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("cost_total", bigint, nullable=True),
        sa.Column("cost_from_provider", sa.Boolean(), nullable=True),
        sa.Column("stop_reason", sa.String(24), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.UniqueConstraint("turn_id", "call_seq", name="uq_cur_turn_seq"),
        sa.CheckConstraint("kind IN ('assistant', 'tool_result', 'other')", name="ck_cur_kind"),
    )
    op.create_index("idx_cur_session", "chat_usage_records", ["session_id"])
    op.create_index("idx_cur_turn", "chat_usage_records", ["turn_id"])


def downgrade() -> None:
    op.drop_index("idx_cur_turn", table_name="chat_usage_records")
    op.drop_index("idx_cur_session", table_name="chat_usage_records")
    op.drop_table("chat_usage_records")
    op.drop_index("idx_ctr_session_created", table_name="chat_turn_runtime")
    op.drop_table("chat_turn_runtime")
    op.drop_table("chat_session_model_prefs")
