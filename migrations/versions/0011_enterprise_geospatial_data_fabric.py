"""Enterprise Geospatial Data Fabric migration

Revision ID: 0011_enterprise_geospatial_data_fabric
Revises: d3e4f5a6b7c8
Create Date: 2026-08-08

Enterprise Geospatial Data Fabric (ADR-0050):
- Creates `data_sources` table for virtualized data source connection profiles.
- Creates `spatial_catalog_items` table for metadata catalog.
- Creates `materializations` table for query materialization provenance.
- Adds composite indices.

The table/column shapes mirror app/models/data_fabric.py exactly, so ORM
inserts from DataFabricManager actually succeed. The source_type CHECK is
intentionally omitted: the manager supports postgis/ogc_api/wfs/wms_wmts/
arcgis/stac/geoparquet/flatgeobuf/pmtiles/s3, and a fixed allow-list would
reject ogc_api and flatgeobuf at insert time.
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
            sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
            sa.Column('owner_id', sa.String(length=255), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('source_type', sa.String(length=50), nullable=False),
            sa.Column('endpoint_url', sa.Text(), nullable=False),
            sa.Column('connection_profile', sa.JSON(), nullable=False),
            sa.Column('capabilities_json', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='active'),
            sa.Column('last_health_check', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('org_id', 'name', name='uq_datasource_org_name'),
        )
        with op.batch_alter_table('data_sources', schema=None) as batch_op:
            batch_op.create_index('idx_datasource_org_type', ['org_id', 'source_type'])
            batch_op.create_index('idx_datasource_status', ['status'])
            batch_op.create_index('ix_data_sources_source_type', ['source_type'])

        op.create_table(
            'spatial_catalog_items',
            sa.Column('id', sa.String(length=255), nullable=False, primary_key=True),
            sa.Column('source_id', sa.String(length=255), sa.ForeignKey('data_sources.id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('geometry_type', sa.String(length=50), nullable=True),
            sa.Column('feature_type', sa.String(length=50), nullable=False, server_default='vector'),
            sa.Column('crs', sa.String(length=50), nullable=True, server_default='EPSG:4326'),
            sa.Column('bbox_json', sa.JSON(), nullable=True),
            sa.Column('tags_json', sa.JSON(), nullable=False),
            sa.Column('descriptor_json', sa.JSON(), nullable=False),
            sa.Column('meta_profile_json', sa.JSON(), nullable=False),
            sa.Column('fingerprint', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        with op.batch_alter_table('spatial_catalog_items', schema=None) as batch_op:
            batch_op.create_index('idx_catalog_source_name', ['source_id', 'name'])
            batch_op.create_index('ix_spatial_catalog_items_name', ['name'])
            batch_op.create_index('ix_spatial_catalog_items_source_id', ['source_id'])
            batch_op.create_index('ix_spatial_catalog_items_geometry_type', ['geometry_type'])
            batch_op.create_index('ix_spatial_catalog_items_feature_type', ['feature_type'])
            batch_op.create_index('idx_catalog_geom_feature', ['geometry_type', 'feature_type'])

        op.create_table(
            'materializations',
            sa.Column('id', sa.String(length=255), nullable=False, primary_key=True),
            sa.Column('dataset_id', sa.String(length=255), nullable=False),
            sa.Column('source_id', sa.String(length=255), sa.ForeignKey('data_sources.id', ondelete='SET NULL'), nullable=True),
            sa.Column('ref_id', sa.String(length=255), nullable=False),
            sa.Column('query_spec_json', sa.JSON(), nullable=False),
            sa.Column('fingerprint', sa.String(length=255), nullable=True),
            sa.Column('record_count', sa.Integer(), nullable=True),
            sa.Column('materialized_at', sa.DateTime(), nullable=True),
        )
        with op.batch_alter_table('materializations', schema=None) as batch_op:
            batch_op.create_index('idx_mat_dataset_ref', ['dataset_id', 'ref_id'])
            batch_op.create_index('ix_materializations_dataset_id', ['dataset_id'])
            batch_op.create_index('ix_materializations_ref_id', ['ref_id'])
    else:
        op.execute("""
            CREATE TABLE IF NOT EXISTS data_sources (
                id VARCHAR(255) PRIMARY KEY,
                org_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
                owner_id VARCHAR(255) REFERENCES users(id) ON DELETE SET NULL,
                name VARCHAR(255) NOT NULL,
                source_type VARCHAR(50) NOT NULL,
                endpoint_url TEXT NOT NULL,
                connection_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                capabilities_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                status VARCHAR(50) NOT NULL DEFAULT 'active',
                last_health_check TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_datasource_org_name UNIQUE (org_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_datasource_org_type ON data_sources(org_id, source_type);
            CREATE INDEX IF NOT EXISTS idx_datasource_status ON data_sources(status);
            CREATE INDEX IF NOT EXISTS ix_data_sources_source_type ON data_sources(source_type);

            CREATE TABLE IF NOT EXISTS spatial_catalog_items (
                id VARCHAR(255) PRIMARY KEY,
                source_id VARCHAR(255) NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                title VARCHAR(255),
                description TEXT,
                geometry_type VARCHAR(50),
                feature_type VARCHAR(50) NOT NULL DEFAULT 'vector',
                crs VARCHAR(50) DEFAULT 'EPSG:4326',
                bbox_json JSONB,
                tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                descriptor_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                meta_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                fingerprint VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_catalog_source_name ON spatial_catalog_items(source_id, name);
            CREATE INDEX IF NOT EXISTS ix_spatial_catalog_items_name ON spatial_catalog_items(name);
            CREATE INDEX IF NOT EXISTS ix_spatial_catalog_items_source_id ON spatial_catalog_items(source_id);
            CREATE INDEX IF NOT EXISTS ix_spatial_catalog_items_geometry_type ON spatial_catalog_items(geometry_type);
            CREATE INDEX IF NOT EXISTS ix_spatial_catalog_items_feature_type ON spatial_catalog_items(feature_type);
            CREATE INDEX IF NOT EXISTS idx_catalog_geom_feature ON spatial_catalog_items(geometry_type, feature_type);

            CREATE TABLE IF NOT EXISTS materializations (
                id VARCHAR(255) PRIMARY KEY,
                dataset_id VARCHAR(255) NOT NULL,
                source_id VARCHAR(255) REFERENCES data_sources(id) ON DELETE SET NULL,
                ref_id VARCHAR(255) NOT NULL,
                query_spec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                fingerprint VARCHAR(255),
                record_count INTEGER,
                materialized_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_mat_dataset_ref ON materializations(dataset_id, ref_id);
            CREATE INDEX IF NOT EXISTS ix_materializations_dataset_id ON materializations(dataset_id);
            CREATE INDEX IF NOT EXISTS ix_materializations_ref_id ON materializations(ref_id);
        """)


def downgrade() -> None:
    if _is_sqlite():
        op.drop_table('materializations')
        op.drop_table('spatial_catalog_items')
        op.drop_table('data_sources')
    else:
        op.execute("DROP TABLE IF EXISTS materializations CASCADE;")
        op.execute("DROP TABLE IF EXISTS spatial_catalog_items CASCADE;")
        op.execute("DROP TABLE IF EXISTS data_sources CASCADE;")
