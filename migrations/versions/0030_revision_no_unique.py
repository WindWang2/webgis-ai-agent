"""artifact_revisions: unique (artifact_id, revision_no)

Revision ID: 0030_revision_no_unique
Revises: 0029_geocompute_v5_runtime
Create Date: 2026-09-07

Round-1 review fix（DATA-ARCH MINOR）：并发不同内容修订读到同一 head 时，
``record_revision`` 各自算出同一 ``revision_no`` 并双双落库 —— 账本出现重复
修订号（head 判定 / 展示次序漂移）。本迁移补上模型已声明的唯一索引
``uq_artifact_revision_no``（``app/models/project.ArtifactRevision``），纯
additive，无数据改写（重复行**重编号**而非删除 —— 修订是 append-only 证据）：

- dedup-guard 先行：同 ``(artifact_id, revision_no)`` 组内按
  ``created_at, id`` 保留最早一行，其余依次 bump 到该 artifact 的下一个
  空闲号（唯一索引才建得起来，且行数守恒）；
- SQLite：普通 ``CREATE UNIQUE INDEX`` 即可（无 batch table 重建需求），
  存在性守卫保证重入；PG 同款守卫等价 ``IF NOT EXISTS``。

create_all-coexistence 纪律同 0022-0029（漂移守卫
tests/test_deploy_migration_wiring.py 按列元组比对模型索引）。downgrade 只
删索引（重编号不可逆 —— 号是历史证据，绝不回写）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0030_revision_no_unique"
down_revision: Union[str, Sequence[str], None] = "0029_geocompute_v5_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "artifact_revisions"
_INDEX = "uq_artifact_revision_no"


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _dedup_revision_numbers(bind) -> int:
    """重编号重复 (artifact_id, revision_no) 组（保留最早行，其余顺延）。

    有界：单条全表 ORDER BY 扫描（账本是每 artifact ≤ 若干修订的有界
    行集），内存 O(组数)。返回重编号行数（诊断用）。
    """
    rows = bind.execute(sa.text(
        "SELECT id, artifact_id, revision_no FROM artifact_revisions "
        "ORDER BY artifact_id, revision_no, created_at, id"
    )).fetchall()
    seen: dict = {}
    fixed = 0
    for rid, aid, no in rows:
        used = seen.setdefault(aid, set())
        if no in used:
            new_no = (max(used) if used else 0) + 1
            while new_no in used:  # 防御：极端漂移下顺延到真正空闲的号
                new_no += 1
            bind.execute(
                sa.text(
                    "UPDATE artifact_revisions SET revision_no = :n WHERE id = :i"
                ),
                {"n": new_no, "i": rid},
            )
            used.add(new_no)
            fixed += 1
        else:
            used.add(no)
    return fixed


def upgrade() -> None:
    bind = op.get_bind()
    # dedup-guard 先行：唯一索引在有重复行时必然失败（索引创建是原子的，
    # 失败即整个迁移回滚 —— 先把历史漂移修正掉）。
    _dedup_revision_numbers(bind)
    if not _index_exists(_TABLE, _INDEX):
        op.create_index(
            _INDEX, _TABLE, ["artifact_id", "revision_no"], unique=True
        )


def downgrade() -> None:
    if _index_exists(_TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
