"""layers.org_id FK 对齐模型 ondelete=CASCADE（#2205 存量库修复）

Revision ID: 0108_layers_org_fk_ondelete
Revises: dc0b99ededbd
Create Date: 2026-11-18

背景：模型自始声明 ``Layer.org_id = ForeignKey(..., ondelete="CASCADE")``
（db_model.py），但初始建表迁移遗漏 ON DELETE，e46935cd5dd1 的
upgrade 也只补了 creator_id 等约束 —— 已跑过该迁移的 PG 库
``layers_org_id_fkey`` 停在 NO ACTION，删组织时图层行残留。

e46935cd5dd1 / 初始建表已在同批修复（新装库正确）；本迁移面向**存量库**：
幂等重建该 FK 为级联形（DROP IF EXISTS + ADD，重试安全），downgrade
对称恢复非级联形（与本迁移 upgrade 前一致）。

SQLite：连接从不开启 PRAGMA foreign_keys（app/core/database.py），且
初始建表已带级联声明 —— 本迁移对 SQLite 为空操作。
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = '0108_layers_org_fk_ondelete'
down_revision: Union[str, Sequence[str], None] = 'dc0b99ededbd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = 'layers'
_FKEY = 'layers_org_id_fkey'


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == 'sqlite'


def upgrade() -> None:
    if _is_sqlite():
        return  # FK 不执行 + 新装库已有级联声明；无操作
    # DROP IF EXISTS + ADD：半途失败重试安全（FK 约束无 IF NOT EXISTS）。
    op.execute(f"""
        ALTER TABLE {_TABLE}
            DROP CONSTRAINT IF EXISTS {_FKEY},
            ADD CONSTRAINT {_FKEY}
                FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
    """)


def downgrade() -> None:
    if _is_sqlite():
        return
    op.execute(f"""
        ALTER TABLE {_TABLE}
            DROP CONSTRAINT IF EXISTS {_FKEY},
            ADD CONSTRAINT {_FKEY}
                FOREIGN KEY (org_id) REFERENCES organizations(id)
    """)
