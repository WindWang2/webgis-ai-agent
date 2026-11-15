"""G43: turn context receipts + session source policies (ADR-0295)

Revision ID: g43_turn_context_receipts
Revises: g40_cartographic_symbol_assets
Create Date: 2026-11-15

纯加性三件事（无数据迁移）：
- ``turn_context_receipts``：每 Pi turn 的实际装配回执（状态机 assembled/
  not_sent/submitted/unknown + disposition + turn_outcome 独立记录；payload
  只存有界投影）；
- ``session_source_policies``：会话级可选资料策略（mode/preferred/excluded
  均为服务端铸造源 id；policy_version 为 CAS 令牌）；
- ``messages.context_receipt``：用户消息的有界回执摘要（NULL = 无回执，
  存量行零改动）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g43_turn_context_receipts"
down_revision: Union[str, Sequence[str], None] = "g40_cartographic_symbol_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "turn_context_receipts",
        sa.Column("id", bigint, primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False, unique=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("org_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("assembly_mode", sa.String(16), nullable=False, server_default="typed"),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="assembled"),
        sa.Column("not_sent_reason", sa.String(48), nullable=True),
        sa.Column("disposition", sa.String(16), nullable=True),
        sa.Column("turn_outcome", sa.String(16), nullable=True),
        sa.Column("user_message_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.CheckConstraint(
            "status IN ('assembled', 'not_sent', 'submitted', 'unknown')",
            name="ck_tcr_status"),
        sa.CheckConstraint(
            "disposition IN ('started', 'queued', 'handled')",
            name="ck_tcr_disposition"),
        sa.CheckConstraint(
            "turn_outcome IN ('completed', 'failed', 'cancelled')",
            name="ck_tcr_turn_outcome"),
    )
    op.create_index(
        "idx_tcr_session_created", "turn_context_receipts", ["session_id", "created_at"])

    op.create_table(
        "session_source_policies",
        sa.Column("session_id", sa.String(255), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("project_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("mode", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("preferred", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.CheckConstraint("mode IN ('auto', 'explicit')", name="ck_ssp_mode"),
    )

    op.add_column(
        "messages", sa.Column("context_receipt", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "context_receipt")
    op.drop_table("session_source_policies")
    op.drop_index("idx_tcr_session_created", table_name="turn_context_receipts")
    op.drop_table("turn_context_receipts")
