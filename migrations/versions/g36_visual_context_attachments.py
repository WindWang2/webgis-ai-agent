"""G36 visual context: message attachments metadata column

Revision ID: g36_visual_context
Revises: 0106_lifecycle_policy_org_unique
Create Date: 2026-11-14 15:55:00.000000

G36（ADR-0291）：用户消息的视觉附件元数据（evidence refs + 有界摘要；
只存 ref/元数据，绝不存 base64）。NULL = 无附件（存量行零改动，加性列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g36_visual_context'
down_revision: Union[str, Sequence[str], None] = '0106_lifecycle_policy_org_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('messages', sa.Column('attachments', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'attachments')
