"""G30: skill job parameter presets table

Revision ID: g3001_skill_job
Revises: 0104_map_briefs
Create Date: 2026-11-12

可复用技能作业配置引用（params + 角色绑定模板 + 技能资产身份）。
预设不承载数据内容/凭据；重用 = 重新绑定 + 重新 prepare。纯新表，
无数据迁移（additive）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g3001_skill_job"
down_revision: Union[str, Sequence[str], None] = "0104_map_briefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_job_presets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "org_id", sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "creator_id", sa.String(255),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(255), nullable=True, server_default=""),
        sa.Column("session_id", sa.String(255), nullable=True, server_default=""),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(400), nullable=True, server_default=""),
        sa.Column("skill_id", sa.String(80), nullable=False),
        sa.Column("skill_version", sa.String(16), nullable=False),
        sa.Column("skill_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("bindings_template", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index(
        "idx_skill_preset_org_project", "skill_job_presets",
        ["org_id", "project_id"])


def downgrade() -> None:
    op.drop_index("idx_skill_preset_org_project", table_name="skill_job_presets")
    op.drop_table("skill_job_presets")
