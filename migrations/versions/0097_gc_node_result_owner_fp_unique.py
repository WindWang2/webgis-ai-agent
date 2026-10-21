"""geocompute_node_results: (owner_scope, node_fingerprint) 真正唯一（#1724）

Revision ID: 0097_gc_node_result_owner_fp_unique
Revises: 0096_harness_turn_journal
Create Date: 2026-10-20

深审 #1724：模型 ``GeoComputeNodeResult`` 声明
``Index("uq_gc_node_result_owner_fp", "owner_scope", "node_fingerprint",
unique=True)``（app/models/db_model.py:315，docstring「重写即刷新」），但
0029 建成的是**非唯一**索引（0029_geocompute_v5_runtime.py:71 漏
unique=True）—— create_all 库有唯一约束、alembic 链（prod 真相）没有，
契约在库里完全不存在。后果：reuse_index.record_result 的 read→delete→
insert 跨 worker 非原子，并发同键写入静默产生重复行。

本迁移把既有索引 drop + 重建为唯一（0030_revision_no_unique 同款治理）：

- dedup-guard 先行：同 ``(owner_scope, node_fingerprint)`` 组内保留
  **最新**一行（MAX(id) —— 自增序即写入序），其余删除。这是缓存索引而
  非证据账本，「重写即刷新」语义下最新行才是有效 checkpoint（与 0030
  保留最早行的 revision 证据语义相反）；无重复时 DELETE 命中 0 行。
- 存在性/唯一性双守卫：索引缺失 → 建唯一；已存在但非唯一 → drop+create
  唯一；已唯一（create_all 库）→ 空转（可重入）。
- 双方言同一 SQL（MAX(id) 分组 DELETE 在 SQLite/PG 等价），无 batch
  表重建需求（0030 同款结论）。

downgrade 回到 0029 的非唯一形态（drop unique + create 非唯一）——
重复行本身不可逆恢复，如实降级。
"""
from typing import Sequence, Union

import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0097_gc_node_result_owner_fp_unique"
down_revision: Union[str, Sequence[str], None] = "0096_harness_turn_journal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_TABLE = "geocompute_node_results"
_INDEX = "uq_gc_node_result_owner_fp"
_COLS = ["owner_scope", "node_fingerprint"]


def _index_state(bind) -> Union[str, None]:
    """索引现状：None=不存在 / "unique" / "non_unique"。"""
    for idx in sa.inspect(bind).get_indexes(_TABLE):
        if idx["name"] == _INDEX:
            return "unique" if idx.get("unique") else "non_unique"
    return None


def _dedup_owner_fingerprint(bind) -> int:
    """删除同 (owner_scope, node_fingerprint) 的陈旧重复行（保留最新）。"""
    result = bind.execute(sa.text(
        f"DELETE FROM {_TABLE} "
        "WHERE id NOT IN ("
        "  SELECT MAX(id) FROM geocompute_node_results "
        "  GROUP BY owner_scope, node_fingerprint"
        ")"
    ))
    return int(result.rowcount or 0)


def upgrade() -> None:
    bind = op.get_bind()
    # dedup-guard 先行：唯一索引在重复行上必然创建失败（原子回滚）。
    removed = _dedup_owner_fingerprint(bind)
    if removed:
        logger.info(
            "[%s] dedup: 删除 %d 行陈旧重复 (owner_scope, node_fingerprint)",
            _TABLE, removed,
        )
    state = _index_state(bind)
    if state is None:
        op.create_index(_INDEX, _TABLE, _COLS, unique=True)
    elif state == "non_unique":
        op.drop_index(_INDEX, table_name=_TABLE)
        op.create_index(_INDEX, _TABLE, _COLS, unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    state = _index_state(bind)
    if state == "unique":
        op.drop_index(_INDEX, table_name=_TABLE)
        op.create_index(_INDEX, _TABLE, _COLS)
    elif state is None:
        op.create_index(_INDEX, _TABLE, _COLS)
