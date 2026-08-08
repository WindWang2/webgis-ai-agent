"""Create project workspace, dataset, workflow, workflow run, artifact, and lineage tables

Revision ID: 0010_project_workspace_workflow
Revises: d3e4f5a6b7c8
Create Date: 2026-08-08

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010_project_workspace_workflow'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. projects
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('owner_id', sa.String(length=255), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived', 'deleted')", name="ck_project_status"),
    )
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.create_index('idx_project_org_id', ['org_id'])
        batch_op.create_index('idx_project_owner_id', ['owner_id'])
        batch_op.create_index('idx_project_status', ['status'])

    # 2. project_datasets
    op.create_table(
        'project_datasets',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=255), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('source_type', sa.String(length=50), nullable=False),
        sa.Column('source_ref', sa.String(length=255), nullable=True),
        sa.Column('schema_profile', sa.JSON(), nullable=True),
        sa.Column('crs', sa.String(length=100), nullable=True, server_default='EPSG:4326'),
        sa.Column('quality_status', sa.String(length=20), nullable=True, server_default='unchecked'),
        sa.Column('version_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "quality_status IN ('unchecked', 'valid', 'invalid', 'warning', 'unknown', 'pending', 'verified')",
            name="ck_project_dataset_quality_status",
        ),
    )
    with op.batch_alter_table('project_datasets', schema=None) as batch_op:
        batch_op.create_index('idx_project_dataset_project_id', ['project_id'])
        batch_op.create_index('idx_project_dataset_source_type', ['source_type'])

    # 3. workflows
    op.create_table(
        'workflows',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=255), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('graph_spec', sa.JSON(), nullable=True),
        sa.Column('inputs_schema', sa.JSON(), nullable=True),
        sa.Column('created_from_session', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_workflow_version_pos"),
    )
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.create_index('idx_workflow_project_id', ['project_id'])
        batch_op.create_index('idx_workflow_session', ['created_from_session'])

    # 4. workflow_runs
    op.create_table(
        'workflow_runs',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('workflow_id', sa.String(length=255), sa.ForeignKey('workflows.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workflow_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('input_bindings', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('execution_trace', sa.JSON(), nullable=True),
        sa.Column('outputs', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('cost_perf_summary', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_workflow_run_status",
        ),
    )
    with op.batch_alter_table('workflow_runs', schema=None) as batch_op:
        batch_op.create_index('idx_workflow_run_workflow_id', ['workflow_id'])
        batch_op.create_index('idx_workflow_run_status', ['status'])

    # 5. artifacts
    op.create_table(
        'artifacts',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=255), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('artifact_type', sa.String(length=50), nullable=False),
        sa.Column('format', sa.String(length=50), nullable=True),
        sa.Column('crs', sa.String(length=100), nullable=True, server_default='EPSG:4326'),
        sa.Column('storage_ref', sa.String(length=500), nullable=True),
        sa.Column('upload_record_id', sa.Integer(), sa.ForeignKey('uploads.id', ondelete='SET NULL'), nullable=True),
        sa.Column('layer_id', sa.BigInteger(), sa.ForeignKey('layers.id', ondelete='SET NULL'), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.create_index('idx_artifact_project_id', ['project_id'])
        batch_op.create_index('idx_artifact_type', ['artifact_type'])
        batch_op.create_index('idx_artifact_layer_id', ['layer_id'])
        batch_op.create_index('idx_artifact_upload_record_id', ['upload_record_id'])

    # 6. artifact_lineages
    op.create_table(
        'artifact_lineages',
        sa.Column('id', sa.String(length=255), primary_key=True, nullable=False),
        sa.Column('artifact_id', sa.String(length=255), sa.ForeignKey('artifacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('parent_artifact_id', sa.String(length=255), sa.ForeignKey('artifacts.id', ondelete='CASCADE'), nullable=True),
        sa.Column('producing_tool', sa.String(length=100), nullable=True),
        sa.Column('tool_version', sa.String(length=50), nullable=True),
        sa.Column('workflow_run_id', sa.String(length=255), sa.ForeignKey('workflow_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('parameters', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table('artifact_lineages', schema=None) as batch_op:
        batch_op.create_index('idx_lineage_artifact_id', ['artifact_id'])
        batch_op.create_index('idx_lineage_parent_artifact_id', ['parent_artifact_id'])
        batch_op.create_index('idx_lineage_workflow_run_id', ['workflow_run_id'])


def downgrade() -> None:
    op.drop_table('artifact_lineages')
    op.drop_table('artifacts')
    op.drop_table('workflow_runs')
    op.drop_table('workflows')
    op.drop_table('project_datasets')
    op.drop_table('projects')
