"""Lifecycle policies are unique per organization, not globally.

Revision ID: 0106_lifecycle_policy_org_unique
Revises: e6fe5ade5010
Create Date: 2026-11-13

Org admins must not rewrite the shared default row. Name stays unique
inside one org (org_id NULL = the global default).

#2209 修订：原实现用 UniqueConstraint(org_id, name) —— 唯一约束中 NULL
互不相等（PG/SQLite 同），org_id IS NULL 的全局行可同名插入多行，与
「全局默认名全局唯一」的语义相悖。改用 COALESCE 表达式唯一索引
（NULL → 哨兵 -1，organizations.id 自增恒正），与模型 data_lifecycle.py
的 Index(coalesce(org_id, -1), name, unique=True) 同形。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0106_lifecycle_policy_org_unique"
down_revision: Union[str, Sequence[str], None] = "e6fe5ade5010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "lifecycle_policies"
_OLD = "uq_lifecycle_policy_name"
_NEW = "uq_lifecycle_policy_org_name"


def _unique_names(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    if table not in set(insp.get_table_names()):
        return set()
    names = {uq.get("name") for uq in insp.get_unique_constraints(table)}
    names.update(ix.get("name") for ix in insp.get_indexes(table))
    return {name for name in names if name}


def _index_columns(table: str, index_name: str) -> str | None:
    """已有同名索引的建索引表达式（区分 COALESCE 形与朴素 (org_id, name) 形）。"""
    insp = sa.inspect(op.get_bind())
    for ix in insp.get_indexes(table):
        if ix.get("name") == index_name:
            cols = ix.get("column_names") or []
            # 表达式索引（create_all 按模型建的 COALESCE 形）在 PG 反射里
            # column_names 含 None（表达式元素）—— 直接 join 会 TypeError。
            # 它不是朴素 (org_id, name) 形，返回 None 让调用方判为「无需替换」。
            if any(c is None for c in cols):
                return None
            return ",".join(cols)
    return None


def _is_unique_constraint(table: str, name: str) -> bool:
    """``name`` 是否是表级 UNIQUE 约束（而非独立索引）。

    commit d3695d42a 的旧版 0106 用 ``batch.create_unique_constraint`` 建的是
    **约束**；PG 反射把约束的支撑索引也报进 ``get_indexes``（PG 方言只过滤
    indisprimary），此时 ``DROP INDEX`` 会被 PG 以 DEPENDENCY_INTERNAL 拒绝
    （"cannot drop index ... because constraint ... requires it"）—— 必须
    ``DROP CONSTRAINT``。
    """
    insp = sa.inspect(op.get_bind())
    return any(
        uq.get("name") == name for uq in insp.get_unique_constraints(table)
    )


def _is_plain_org_name_unique(table: str, name: str) -> bool:
    """``name`` 是否以朴素 ``(org_id, name)`` 唯一形态在场。

    旧版 0106 的 ``create_unique_constraint`` 在 PG 上落成约束（+同名支撑
    索引）、在 SQLite 上落成表级 UNIQUE（``get_indexes`` 看不见），两种形态
    都要能识别，否则 COALESCE 表达式索引建不出来、修复断链。
    """
    insp = sa.inspect(op.get_bind())
    for uq in insp.get_unique_constraints(table):
        if uq.get("name") == name and (uq.get("column_names") or []) == ["org_id", "name"]:
            return True
    return _index_columns(table, name) == "org_id,name"


def upgrade() -> None:
    if _TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if _OLD in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_OLD, type_="unique")
    # 旧版本迁移可能已建过朴素唯一约束/索引 —— 先去掉再建 COALESCE 形。
    if _is_plain_org_name_unique(_TABLE, _NEW):
        with op.batch_alter_table(_TABLE) as batch:
            if _is_unique_constraint(_TABLE, _NEW):
                batch.drop_constraint(_NEW, type_="unique")
            else:
                batch.drop_index(_NEW)
    if _NEW not in _unique_names(_TABLE):
        # 表达式唯一索引：NULL org_id 归一到 -1，全局默认名全局唯一（#2209）。
        # PG/SQLite 均支持表达式索引；IF NOT EXISTS 保证重试幂等。
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_lifecycle_policy_org_name "
            "ON lifecycle_policies (COALESCE(org_id, -1), name)"
        )


def downgrade() -> None:
    if _TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if _NEW in _unique_names(_TABLE):
        op.execute("DROP INDEX IF EXISTS uq_lifecycle_policy_org_name")
    if _OLD not in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_unique_constraint(_OLD, ["name"])
