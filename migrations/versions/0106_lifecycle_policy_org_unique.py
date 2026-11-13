"""Lifecycle policies are unique per organization, not globally.

Revision ID: 0106_lifecycle_policy_org_unique
Revises: e6fe5ade5010
Create Date: 2026-11-13

Org admins must not rewrite the shared default row. Name stays unique
inside one org (org_id NULL = the global default).
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


def upgrade() -> None:
    if _TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if _OLD in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_OLD, type_="unique")
    if _NEW not in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_unique_constraint(_NEW, ["org_id", "name"])


def downgrade() -> None:
    if _TABLE not in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    if _NEW in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_NEW, type_="unique")
    if _OLD not in _unique_names(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_unique_constraint(_OLD, ["name"])
