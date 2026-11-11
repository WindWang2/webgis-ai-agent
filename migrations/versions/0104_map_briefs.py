"""G11: map_briefs / map_brief_releases（证据化地图简报存储面）

Revision ID: 0104_map_briefs
Revises: 0103_workflow_run_session
Create Date: 2026-11-08

ADR-0288：简报 = 会话作用域可编辑草稿（map_briefs，revision 单调）+
不可变发布版本（map_brief_releases）。release 指向内容寻址 CAS 载荷
（content_sha256 幂等键），brief 行删除后 release 保留（BRF-O11：旧版
可读取）—— 故 brief_id 外键 ON DELETE SET NULL 并冗余 conversation_id
做所有权解析。约定照抄 0099 先例：additive 新表 + create_all
coexistence guard 可重入 DDL；downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0104_map_briefs"
down_revision: Union[str, Sequence[str], None] = "0103_workflow_run_session"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BRIEF_TABLE = "map_briefs"
_RELEASE_TABLE = "map_brief_releases"


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists(_BRIEF_TABLE):
        op.create_table(
            _BRIEF_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "conversation_id", sa.String(length=255),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=255), nullable=False),
            # MapBriefSpec JSON 序列（服务层限 ≤512KB，表不做形状断言）。
            sa.Column("spec", sa.Text(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("conversation_id", name="uq_mb_conversation"),
            sa.CheckConstraint("revision >= 1", name="ck_mb_revision"),
        )
    if not _table_exists(_RELEASE_TABLE):
        op.create_table(
            _RELEASE_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            # 草稿删除后 release 保留：SET NULL + conversation_id 冗余归属。
            sa.Column(
                "brief_id", sa.String(length=64),
                sa.ForeignKey("map_briefs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "conversation_id", sa.String(length=255),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("release_no", sa.Integer(), nullable=False),
            sa.Column("frozen_revision", sa.Integer(), nullable=False),
            # ready|failed —— 生成失败的文件不列 ready（BRF-O05）。
            sa.Column("status", sa.String(length=16), nullable=False),
            # 发布 manifest JSON：files/evidence/dataset_versions/指纹/draft 标记。
            sa.Column("manifest", sa.Text(), nullable=False),
            # canonical spec 内容哈希 —— 同输入重复提交幂等返回既有 release。
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("html_object_key", sa.String(length=255), nullable=False),
            sa.Column("share_code", sa.String(length=64), nullable=True),
            sa.Column("share_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("brief_id", "release_no",
                                name="uq_mbr_brief_release"),
            sa.UniqueConstraint("content_sha256", name="uq_mbr_content"),
            sa.UniqueConstraint("share_code", name="uq_mbr_share_code"),
        )


def downgrade() -> None:
    if _table_exists(_RELEASE_TABLE):
        op.drop_table(_RELEASE_TABLE)
    if _table_exists(_BRIEF_TABLE):
        op.drop_table(_BRIEF_TABLE)
