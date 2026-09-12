"""V9 安全与多租户（ADR-0139 P6）：refresh_token_families 表

refresh token 轮换 + 重用检测：登录/注册建家族（``fam`` claim），
refresh 轮换前移 current_jti；携带旧 jti 的重放使**整个家族**失效
（reuse_detected=True），阻断被盗令牌的互踢循环。

downgrade 丢表 = 回到 soft rotation（所有在飞 ``fam`` claim 的 refresh
退化为 legacy 路径，≤7d 自然过期）——安全回退面诚实披露。

Revision ID: 0041_security_v9_refresh_families
Revises: 0040_security_v9_audit_events
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0041_security_v9_refresh_families'
down_revision: Union[str, Sequence[str], None] = '0040_security_v9_audit_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'refresh_token_families'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('family_id', sa.String(length=32), nullable=False),
        sa.Column('current_jti', sa.String(length=32), nullable=False),
        sa.Column('reuse_detected', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('rotated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('family_id', name='uq_refresh_family_id'),
    )
    op.create_index('idx_refresh_family_user', TABLE, ['user_id', 'family_id'])
    op.create_index('idx_refresh_family_jti', TABLE, ['current_jti'])


def downgrade() -> None:
    op.drop_index('idx_refresh_family_jti', table_name=TABLE)
    op.drop_index('idx_refresh_family_user', table_name=TABLE)
    op.drop_table(TABLE)
