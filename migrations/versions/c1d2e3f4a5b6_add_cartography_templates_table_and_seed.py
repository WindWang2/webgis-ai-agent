"""Add cartography_templates table and seed built-in templates

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-07-30

Cartography Template System (tk1 #185):
- Creates `cartography_templates` table supporting 4 template kinds: basemap, symbology, layout, thematic.
- Seeds ~18 built-in templates (is_builtin=True).
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa

from app.schemas.template_schema import SEED_TEMPLATES


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    # 1. Create table if not exists
    if _is_sqlite():
        op.create_table(
            'cartography_templates',
            sa.Column('id', sa.String(length=255), nullable=False, primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
            sa.Column('creator_id', sa.String(length=255), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('kind', sa.String(length=50), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=True),
            sa.Column('keywords', sa.JSON(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.CheckConstraint("kind IN ('basemap', 'symbology', 'layout', 'thematic')", name="ck_template_kind"),
        )
        with op.batch_alter_table('cartography_templates', schema=None) as batch_op:
            batch_op.create_index('idx_template_kind', ['kind'])
            batch_op.create_index('idx_template_name', ['name'])
            batch_op.create_index('idx_template_category', ['category'])
            batch_op.create_index('idx_template_builtin', ['is_builtin'])
            batch_op.create_index('idx_template_builtin_kind', ['is_builtin', 'kind'])
            batch_op.create_index('idx_template_org_kind', ['org_id', 'kind'])
    else:
        op.execute("""
            CREATE TABLE IF NOT EXISTS cartography_templates (
                id VARCHAR(255) PRIMARY KEY,
                org_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
                creator_id VARCHAR(255) REFERENCES users(id) ON DELETE SET NULL,
                kind VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                category VARCHAR(100),
                keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
                description TEXT,
                payload JSONB NOT NULL,
                is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_template_kind CHECK (kind IN ('basemap', 'symbology', 'layout', 'thematic'))
            );
            CREATE INDEX IF NOT EXISTS idx_template_kind ON cartography_templates(kind);
            CREATE INDEX IF NOT EXISTS idx_template_name ON cartography_templates(name);
            CREATE INDEX IF NOT EXISTS idx_template_category ON cartography_templates(category);
            CREATE INDEX IF NOT EXISTS idx_template_builtin ON cartography_templates(is_builtin);
            CREATE INDEX IF NOT EXISTS idx_template_builtin_kind ON cartography_templates(is_builtin, kind);
            CREATE INDEX IF NOT EXISTS idx_template_org_kind ON cartography_templates(org_id, kind);
        """)

    # 2. Seed built-in templates
    meta = sa.MetaData()
    meta.reflect(bind=op.get_bind(), only=['cartography_templates'])
    templates_table = sa.Table('cartography_templates', meta)

    now = datetime.now(timezone.utc)
    for tmpl in SEED_TEMPLATES:
        op.execute(
            templates_table.insert().values(
                id=tmpl["id"],
                org_id=None,
                creator_id=None,
                kind=tmpl["kind"],
                name=tmpl["name"],
                category=tmpl.get("category"),
                keywords=tmpl.get("keywords", []),
                description=tmpl.get("description"),
                payload=tmpl["payload"],
                is_builtin=tmpl["is_builtin"],
                version=tmpl.get("version", 1),
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    if _is_sqlite():
        op.drop_table('cartography_templates')
    else:
        op.execute("DROP TABLE IF EXISTS cartography_templates CASCADE;")
