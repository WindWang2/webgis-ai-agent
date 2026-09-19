"""Model↔migration CHECK-constraint drift closure (DATA-06)

Revision ID: 0094_model_check_constraint_drift
Revises: 0093_project_knowledge_entries
Create Date: 2026-09-19

深度 review（DATA-06）实测：模型层声明的以下 10 条 CHECK 约束没有任何
迁移创建 —— 生产 Postgres 从建表起就没有它们（create_all 路径只在全新
库生效，而 prod 由 alembic 建链）：

  1. users.ck_user_role
  2. layers.ck_layer_type / ck_layer_visibility / ck_layer_status
  3. layer_permissions.ck_permission
  4. messages.ck_message_role
  5. reports.ck_report_format / ck_report_status
  6. uploads.ck_upload_file_type / ck_upload_format

谓词与模型 ``__table_args__`` 一字不差（drift 的收敛方向以模型为准）。

双方言策略（repo 惯例，cf. 0013/a1b2c3d4e5f6）：

- SQLite：``batch_alter_table`` 重建表补约束；每条约束先经 inspector
  存在性守卫（create_all 库可能已有），幂等。
- PostgreSQL：存在性守卫的 DO 块 + ``ADD CONSTRAINT ... NOT VALID``；
  随后在 savepoint 内尽力 ``VALIDATE``（存量脏数据不阻断升级 —— 约束
  对**新写入**仍然强制，验证失败仅记录告警，状态保持 NOT VALID 诚实披露）。

downgrade 成对删除（SQLite batch / PG IF EXISTS），可重复执行。
"""
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa


revision: str = "0094_model_check_constraint_drift"
down_revision: Union[str, Sequence[str], None] = "0093_project_knowledge_entries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

#: (table, constraint name, SQL predicate) —— 与模型 CHECK 声明一致。
_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("users", "ck_user_role",
     "role IN ('viewer', 'editor', 'admin')"),
    ("layers", "ck_layer_type",
     "layer_type IN ('vector', 'raster', 'tile')"),
    ("layers", "ck_layer_visibility",
     "visibility IN ('org', 'public', 'private')"),
    ("layers", "ck_layer_status",
     "status IN ('pending', 'processing', 'ready', 'error')"),
    ("layer_permissions", "ck_permission",
     "permission IN ('read', 'write', 'admin')"),
    ("messages", "ck_message_role",
     "role IN ('user', 'assistant', 'tool')"),
    ("reports", "ck_report_format",
     "format IN ('pdf', 'html', 'markdown')"),
    ("reports", "ck_report_status",
     "status IN ('pending', 'processing', 'completed', 'failed')"),
    ("uploads", "ck_upload_file_type",
     "file_type IN ('vector', 'raster')"),
    ("uploads", "ck_upload_format",
     "format IN ('geojson', 'shapefile', 'geotiff', 'csv', 'gpkg', 'kml')"),
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _existing_checks(table: str) -> set:
    return {
        ck["name"] for ck in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def upgrade() -> None:
    if _is_sqlite():
        _upgrade_sqlite()
    else:
        _upgrade_postgres()


def downgrade() -> None:
    if _is_sqlite():
        _downgrade_sqlite()
    else:
        _downgrade_postgres()


# ── SQLite ───────────────────────────────────────────────────────────


def _upgrade_sqlite() -> None:
    grouped: dict = {}
    for table, name, expr in _CHECKS:
        if name in _existing_checks(table):
            continue
        grouped.setdefault(table, []).append((name, expr))
    for table, checks in grouped.items():
        with op.batch_alter_table(table, schema=None) as batch:
            for name, expr in checks:
                batch.create_check_constraint(name, expr)


def _downgrade_sqlite() -> None:
    grouped: dict = {}
    for table, name, expr in _CHECKS:
        if name in _existing_checks(table):
            grouped.setdefault(table, []).append((name, expr))
    for table, checks in grouped.items():
        with op.batch_alter_table(table, schema=None) as batch:
            for name, _expr in checks:
                batch.drop_constraint(name, type_="check")


# ── PostgreSQL ────────────────────────────────────────────────────────


def _add_check_postgres(table: str, name: str, expr: str) -> None:
    op.execute(sa.text(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}' "
        f"AND conrelid = '{table}'::regclass) THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID; "
        "END IF; END $$;"
    ))
    # 尽力验证存量行：脏数据不阻断升级（约束对新增/更新仍强制），
    # 验证失败保持 NOT VALID 并留告警。
    try:
        with op.get_bind().begin_nested():
            op.execute(sa.text(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}"))
    except Exception as exc:  # noqa: BLE001 — 存量数据可能违反词表
        logger.warning(
            "CHECK %s.%s 保持 NOT VALID（存量行验证失败：%s）",
            table, name, type(exc).__name__,
        )


def _upgrade_postgres() -> None:
    for table, name, expr in _CHECKS:
        _add_check_postgres(table, name, expr)


def _downgrade_postgres() -> None:
    for table, name, _expr in reversed(_CHECKS):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))
