"""Enterprise Geospatial Data Fabric migration

Revision ID: 0011_enterprise_geospatial_data_fabric
Revises: d3e4f5a6b7c8
Create Date: 2026-08-08

Enterprise Geospatial Data Fabric (ADR-0050):
- Creates `data_sources` table for virtualized data source connection profiles.
- Creates `data_fabric_datasets` table for metadata catalog.
- Creates `data_fabric_audit_logs` table for query pushdown & security audit logs.
- Adds composite indices and check constraints for zero secret leakage & tenant isolation.
"""
from typing import Sequence, Union
from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_enterprise_geospatial_data_fabric'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        op.create_table(
            'data_sources',
            sa.Column('id', sa.String(length=255), nullable=False, primary_key=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('creator_id', sa.String(length=255), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('source_type', sa.String(length=50), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('endpoint_url', sa.String(length=1000), nullable=False),
            sa.Column('auth_type', sa.String(length=50), nullable=False, server_default='none'),
            sa.Column('credentials_encrypted', sa.Text(), nullable=True),
            sa.Column('connection_options', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
            sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('last_health_check_at', sa.DateTime(), nullable=True),
            sa.Column('last_health_status', sa.String(length=20), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('org_id', 'name', name='uq_datasource_org_name'),
            sa.CheckConstraint("auth_type IN ('none', 'basic', 'bearer', 'api_key', 'aws_iam')", name='ck_datasource_auth_type'),
            sa.CheckConstraint("status IN ('active', 'inactive', 'degraded', 'error')", name='ck_datasource_status'),
            sa.CheckConstraint("source_type IN ('postgis', 'wfs', 'wms', 'arcgis', 'stac', 'geoparquet', 'pmtiles', 's3')", name='ck_datasource_source_type'),
        )
        with op.batch_alter_table('data_sources', schema=None) as batch_op:
            batch_op.create_index('idx_datasource_org_type', ['org_id', 'source_type'])
            batch_op.create_index('idx_datasource_status', ['status'])
            batch_op.create_index('idx_datasource_created', ['created_at'])

        op.create_table(
            'data_fabric_datasets',
            sa.Column('id', sa.String(length=255), nullable=False, primary_key=True),
            sa.Column('source_id', sa.String(length=255), sa.ForeignKey('data_sources.id', ondelete='CASCADE'), nullable=False),
            sa.Column('dataset_identifier', sa.String(length=255), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('geometry_type', sa.String(length=50), nullable=True),
            sa.Column('crs', sa.String(length=50), nullable=True, server_default='EPSG:4326'),
            sa.Column('bbox', sa.JSON(), nullable=True),
            sa.Column('feature_count', sa.BigInteger(), nullable=True),
            sa.Column('schema_metadata', sa.JSON(), nullable=False),
            sa.Column('capabilities', sa.JSON(), nullable=False),
            sa.Column('temporal_extent', sa.JSON(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('source_id', 'dataset_identifier', name='uq_dataset_source_identifier'),
        )
        with op.batch_alter_table('data_fabric_datasets', schema=None) as batch_op:
            batch_op.create_index('idx_dataset_source_active', ['source_id', 'is_active'])
            batch_op.create_index('idx_dataset_title', ['title'])
            batch_op.create_index('idx_dataset_geometry_type', ['geometry_type'])

        op.create_table(
            'data_fabric_audit_logs',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, primary_key=True),
            sa.Column('source_id', sa.String(length=255), sa.ForeignKey('data_sources.id', ondelete='SET NULL'), nullable=True),
            sa.Column('dataset_id', sa.String(length=255), sa.ForeignKey('data_fabric_datasets.id', ondelete='SET NULL'), nullable=True),
            sa.Column('user_id', sa.String(length=255), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
            sa.Column('action', sa.String(length=50), nullable=False),
            sa.Column('execution_time_ms', sa.Integer(), nullable=True),
            sa.Column('pushdown_applied', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('records_returned', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('query_summary', sa.JSON(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='success'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.CheckConstraint("action IN ('probe', 'list', 'describe', 'preview', 'query', 'sync')", name='ck_audit_action'),
            sa.CheckConstraint("status IN ('success', 'failed', 'blocked_ssrf', 'unauthorized')", name='ck_audit_status'),
        )
        with op.batch_alter_table('data_fabric_audit_logs', schema=None) as batch_op:
            batch_op.create_index('idx_audit_org_action', ['org_id', 'action'])
            batch_op.create_index('idx_audit_source_created', ['source_id', 'created_at'])
            batch_op.create_index('idx_audit_created', ['created_at'])
    else:
        op.execute("""
            CREATE TABLE IF NOT EXISTS data_sources (
                id VARCHAR(255) PRIMARY KEY,
                org_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                creator_id VARCHAR(255) REFERENCES users(id) ON DELETE SET NULL,
                name VARCHAR(255) NOT NULL,
                source_type VARCHAR(50) NOT NULL,
                description TEXT,
                endpoint_url VARCHAR(1000) NOT NULL,
                auth_type VARCHAR(50) NOT NULL DEFAULT 'none',
                credentials_encrypted TEXT,
                connection_options JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                is_public BOOLEAN NOT NULL DEFAULT FALSE,
                last_health_check_at TIMESTAMP WITH TIME ZONE,
                last_health_status VARCHAR(20),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_datasource_org_name UNIQUE (org_id, name),
                CONSTRAINT ck_datasource_auth_type CHECK (auth_type IN ('none', 'basic', 'bearer', 'api_key', 'aws_iam')),
                CONSTRAINT ck_datasource_status CHECK (status IN ('active', 'inactive', 'degraded', 'error')),
                CONSTRAINT ck_datasource_source_type CHECK (source_type IN ('postgis', 'wfs', 'wms', 'arcgis', 'stac', 'geoparquet', 'pmtiles', 's3'))
            );
            CREATE INDEX IF NOT EXISTS idx_datasource_org_type ON data_sources(org_id, source_type);
            CREATE INDEX IF NOT EXISTS idx_datasource_status ON data_sources(status);
            CREATE INDEX IF NOT EXISTS idx_datasource_created ON data_sources(created_at);

            CREATE TABLE IF NOT EXISTS data_fabric_datasets (
                id VARCHAR(255) PRIMARY KEY,
                source_id VARCHAR(255) NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
                dataset_identifier VARCHAR(255) NOT NULL,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                geometry_type VARCHAR(50),
                crs VARCHAR(50) DEFAULT 'EPSG:4326',
                bbox JSONB,
                feature_count BIGINT,
                schema_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                temporal_extent JSONB,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_dataset_source_identifier UNIQUE (source_id, dataset_identifier)
            );
            CREATE INDEX IF NOT EXISTS idx_dataset_source_active ON data_fabric_datasets(source_id, is_active);
            CREATE INDEX IF NOT EXISTS idx_dataset_title ON data_fabric_datasets(title);
            CREATE INDEX IF NOT EXISTS idx_dataset_geometry_type ON data_fabric_datasets(geometry_type);

            CREATE TABLE IF NOT EXISTS data_fabric_audit_logs (
                id BIGSERIAL PRIMARY KEY,
                source_id VARCHAR(255) REFERENCES data_sources(id) ON DELETE SET NULL,
                dataset_id VARCHAR(255) REFERENCES data_fabric_datasets(id) ON DELETE SET NULL,
                user_id VARCHAR(255) REFERENCES users(id) ON DELETE SET NULL,
                org_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                action VARCHAR(50) NOT NULL,
                execution_time_ms INTEGER,
                pushdown_applied BOOLEAN NOT NULL DEFAULT FALSE,
                records_returned INTEGER NOT NULL DEFAULT 0,
                query_summary JSONB,
                status VARCHAR(20) NOT NULL DEFAULT 'success',
                error_message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_audit_action CHECK (action IN ('probe', 'list', 'describe', 'preview', 'query', 'sync')),
                CONSTRAINT ck_audit_status CHECK (status IN ('success', 'failed', 'blocked_ssrf', 'unauthorized'))
            );
            CREATE INDEX IF NOT EXISTS idx_audit_org_action ON data_fabric_audit_logs(org_id, action);
            CREATE INDEX IF NOT EXISTS idx_audit_source_created ON data_fabric_audit_logs(source_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON data_fabric_audit_logs(created_at);
        """)


def downgrade() -> None:
    if _is_sqlite():
        op.drop_table('data_fabric_audit_logs')
        op.drop_table('data_fabric_datasets')
        op.drop_table('data_sources')
    else:
        op.execute("DROP TABLE IF EXISTS data_fabric_audit_logs CASCADE;")
        op.execute("DROP TABLE IF EXISTS data_fabric_datasets CASCADE;")
        op.execute("DROP TABLE IF EXISTS data_sources CASCADE;")
