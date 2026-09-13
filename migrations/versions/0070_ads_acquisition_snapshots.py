"""ads-v1 Acquisition Snapshots (ADR-0175): version-pin snapshot store.

Revision ID: 0070_ads_acquisition_snapshots
Revises: 0056_cartography_quality_facts
Create Date: 2026-09-13

任务书 adaptive-data-supply/v1 DS5（缺口 A5/A6）：同一请求两次取数拿到同一
份数据；上游改列不再静默出错。

- ``ads_acquisition_snapshots`` —— pin 命中的冻结快照（dataset_key × pin 唯一）：
  内容/schema 指纹 + 修订证据 + 字段表 + 有界负载；漂移检测与影响面分析
  以此为「旧侧」证据源（四维指纹语义沿用 app/lib/data/versioning.py）。
- additive 新表（repo convention）；downgrade 反序回滚，不触碰既有表；
- 号段 0070–0079 为 adaptive-data-supply/v1-master 保留段（领号登记于
  migrations/.alloc.json；V11 段止于 0065，67–69 缓冲）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0070_ads_acquisition_snapshots"
down_revision: Union[str, Sequence[str], None] = "0058_quality_cost_and_wave"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SNAPSHOTS = "ads_acquisition_snapshots"


def upgrade() -> None:
    op.create_table(
        _SNAPSHOTS,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("dataset_key", sa.String(256), nullable=False, index=True),
        sa.Column("pin", sa.String(128), nullable=False),
        sa.Column("version_token", sa.String(128), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("schema_fingerprint", sa.String(64), nullable=True),
        sa.Column("schema_fields_json", sa.Text(), nullable=True),
        sa.Column("revision_json", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dataset_key", "pin", name="uq_ads_snapshot_dataset_pin"),
    )


def downgrade() -> None:
    op.drop_table(_SNAPSHOTS)
