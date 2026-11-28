"""G52 WP3: turn runtime attempt-chain columns (provider retry attempts /
route reason codes / last provider failure kind) + inherit_routed status

Revision ID: g5201_turn_attempts
Revises: aa356ce2fcdc
Create Date: 2026-11-27

GOAL-20261127-01（Pi 自适应执行策略）WP3：attempt 链有界关联（additive）。

- ``chat_turn_runtime`` 增三列：vendor auto-retry 次数、inherit 路由理由码
  （| 分隔，≤8 条）、本 turn 观察到的最后 provider 失败分类
  （provider.FailureKind 词表）—— 不新建账本表，attempt 链与既有
  (turn_id, call_seq) usage 明细、JobOrigin 关联共聚一行。
- ``ck_ctr_status`` 重建：新增 ``inherit_routed``（WP2 健康降级应用，
  与「继承默认」语义区分）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g5201_turn_attempts"
down_revision: Union[str, Sequence[str], None] = "aa356ce2fcdc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_OLD = (
    "status IN ('pending', 'applied', 'inherit_default', 'degraded_inherit', "
    "'failed', 'budget_rejected')"
)
_STATUS_NEW = (
    "status IN ('pending', 'applied', 'inherit_default', 'inherit_routed', "
    "'degraded_inherit', 'failed', 'budget_rejected')"
)


def upgrade() -> None:
    op.add_column(
        "chat_turn_runtime",
        sa.Column("provider_retry_attempts", sa.Integer(), nullable=False,
                  server_default="0"),
    )
    op.add_column(
        "chat_turn_runtime",
        sa.Column("route_reason_codes", sa.String(255), nullable=False,
                  server_default=""),
    )
    op.add_column(
        "chat_turn_runtime",
        sa.Column("last_provider_failure_kind", sa.String(32), nullable=False,
                  server_default=""),
    )
    with op.batch_alter_table("chat_turn_runtime") as batch:
        batch.drop_constraint("ck_ctr_status", type_="check")
        batch.create_check_constraint("ck_ctr_status", _STATUS_NEW)


def downgrade() -> None:
    # 收敛回旧词表前，把新状态值归并到 inherit_default（不丢行）。
    op.execute(
        "UPDATE chat_turn_runtime SET status = 'inherit_default' "
        "WHERE status = 'inherit_routed'"
    )
    with op.batch_alter_table("chat_turn_runtime") as batch:
        batch.drop_constraint("ck_ctr_status", type_="check")
        batch.create_check_constraint("ck_ctr_status", _STATUS_OLD)
    op.drop_column("chat_turn_runtime", "last_provider_failure_kind")
    op.drop_column("chat_turn_runtime", "route_reason_codes")
    op.drop_column("chat_turn_runtime", "provider_retry_attempts")
