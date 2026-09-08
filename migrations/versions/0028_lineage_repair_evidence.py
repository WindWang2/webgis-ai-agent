"""Wave 4 lineage repair evidence: artifact_lineages.repair_evidence + dataset quality vocabulary

Revision ID: 0028_lineage_repair_evidence
Revises: 0027_upload_content_sha256
Create Date: 2026-09-07

目标 D Wave 4（audit 08-lineage-reproducibility-gaps.md §5.1 / §6.2 建议 2-3）：

1. ``artifact_lineages.repair_evidence``（可空 JSON）：**有界、仅摘要** 的
   修复执行证据 —— {plan_id, ops_applied[], op_evidence[],
   issue_codes_addressed[], before/after feature_count, before/after
   content digest}。血缘边继续只存链接与有界事实（红线：绝不做第二个
   产物库），不落任何要素载荷。NULL = 该边不是修复执行产生的。
2. ``project_datasets.quality_status`` 的 CHECK 词表追加 'repairable' 与
   'blocked'：compose_status 四态（valid/warning/repairable/blocked，§七）
   此前无法回写 —— 两值不在旧词表里，写入即违反约束。状态回写是审计
   §6.2 建议 3「close the quality-state loop with existing columns」。

纯新增（一列 + 一个具名 CHECK 的受守卫替换），无数据改写；仅当旧 CHECK
（旧 IN 词表）在场时才替换（create_all-coexistence guard，repo convention，
0022/0024/0027 同款）。SQLite 走 batch 表重建（反射可回具名 CHECK），
PostgreSQL 走原生 ALTER。downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0028_lineage_repair_evidence"
down_revision: Union[str, Sequence[str], None] = "0027_upload_content_sha256"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LINEAGE_TABLE = "artifact_lineages"
_COLUMN = "repair_evidence"

_DATASET_TABLE = "project_datasets"
_CHECK_NAME = "ck_project_dataset_quality_status"
_OLD_VALUES_SQL = (
    "quality_status IN ('unchecked', 'valid', 'invalid', 'warning', "
    "'unknown', 'pending', 'verified')"
)
_NEW_VALUES_SQL = (
    "quality_status IN ('unchecked', 'valid', 'invalid', 'warning', "
    "'unknown', 'pending', 'verified', 'repairable', 'blocked')"
)


def _column_exists(table: str, column: str) -> bool:
    """create_all-coexistence guard（repo convention，0022/0024/0027 同款）。"""
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)
    }


def _check_constraints(table: str) -> dict:
    """具名 CHECK 约束 → {name: sqltext}（SQLite/PG 均可反射具名 CHECK）。"""
    return {
        (c.get("name") or ""): (c.get("sqltext") or "")
        for c in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def _normalized(sqltext: str) -> str:
    return " ".join(str(sqltext).split()).lower()


def _relax_dataset_quality_check() -> None:
    """旧 IN 词表在场 → 替换为含 repairable/blocked 的新词表。

    守卫语义：约束不在场（或已是新词表）→ 幂等跳过 —— create_all 建出的
    库直接带模型里的新词表，绝不重复替换。
    """
    checks = _check_constraints(_DATASET_TABLE)
    sqltext = checks.get(_CHECK_NAME)
    if sqltext is None:
        return
    if "repairable" in _normalized(sqltext):
        return  # 已是新词表（create_all-coexistence）
    with op.batch_alter_table(_DATASET_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.create_check_constraint(_CHECK_NAME, _NEW_VALUES_SQL)


def upgrade() -> None:
    if not _column_exists(_LINEAGE_TABLE, _COLUMN):
        op.add_column(
            _LINEAGE_TABLE,
            sa.Column(_COLUMN, sa.JSON(), nullable=True),
        )
    _relax_dataset_quality_check()


def _restore_dataset_quality_check() -> None:
    """downgrade：恢复旧词表。若已有数据行使用新值（repairable/blocked），
    约束校验会失败 —— 诚实体：降级要求先清退新词表状态，不静默丢证据。"""
    checks = _check_constraints(_DATASET_TABLE)
    sqltext = checks.get(_CHECK_NAME)
    if sqltext is None:
        return
    if "repairable" not in _normalized(sqltext):
        return  # 已是旧词表（幂等）
    with op.batch_alter_table(_DATASET_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.create_check_constraint(_CHECK_NAME, _OLD_VALUES_SQL)


def downgrade() -> None:
    _restore_dataset_quality_check()
    if _column_exists(_LINEAGE_TABLE, _COLUMN):
        op.drop_column(_LINEAGE_TABLE, _COLUMN)
