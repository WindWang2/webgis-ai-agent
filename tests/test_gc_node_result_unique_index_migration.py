"""Issue #1724: geocompute_node_results (owner_scope, node_fingerprint) 唯一索引守卫。

模型 ``GeoComputeNodeResult`` 声明唯一索引（「重写即刷新」），0029 却建成
**非唯一**（0029_geocompute_v5_runtime.py:71 漏 unique=True）—— prod 由
alembic 建链，不变量在库里完全不存在；reuse_index.record_result 的
read→delete→insert 跨 worker 非原子，并发同键写入静默产生重复行。

守卫四件事：
  1. ORM 模型声明唯一索引（create_all 新库直接带上）；
  2. alembic upgrade head 后索引真实存在且**唯一**（重复键插入被拒）；
  3. downgrade → 0096 回到 0029 非唯一形态；灌入重复行后 re-upgrade
     dedup（保留最新行）并重建唯一索引（迁移在存量漂移上可执行）；
  4. 迁移链到 0096 head（防多头）。
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE = "geocompute_node_results"
INDEX_NAME = "uq_gc_node_result_owner_fp"
MIGRATION = "0097_gc_node_result_owner_fp_unique.py"
PREV_HEAD = "0096_harness_turn_journal"

_INSERT = (
    "INSERT INTO geocompute_node_results "
    "(owner_scope, node_fingerprint, result_ref, session_id, org_id, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DATABASE_URL": f"sqlite:///{db_path}",
            "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
            "USE_REDIS": "false",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )


def _index_unique_flags(db_path: Path, table: str) -> dict[str, bool]:
    """PRAGMA index_list → {index_name: is_unique}。"""
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            row[1]: bool(row[2])
            for row in conn.execute(f"PRAGMA index_list({table})")
        }


@pytest.fixture(scope="module")
def head_db(tmp_path_factory) -> Path:
    """模块级跑一次 alembic upgrade head（全链 ~分钟级），各测试复制复用。"""
    db_path = tmp_path_factory.mktemp("gc_unique_idx") / "head.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
    return db_path


def test_model_declares_unique_owner_fp_index():
    """ORM 模型必须声明 (owner_scope, node_fingerprint) 唯一索引。"""
    from app.models.db_model import GeoComputeNodeResult

    indexes = {
        ix.name: (tuple(col.name for col in ix.columns), bool(ix.unique))
        for ix in GeoComputeNodeResult.__table__.indexes
    }
    assert INDEX_NAME in indexes, (
        f"模型缺少索引 {INDEX_NAME}；实际: {sorted(indexes)}"
    )
    cols, unique = indexes[INDEX_NAME]
    assert cols == ("owner_scope", "node_fingerprint")
    assert unique, f"{INDEX_NAME} 必须 unique=True（重写即刷新契约）"


def test_upgrade_head_creates_unique_index(head_db, tmp_path):
    """upgrade head 后索引存在且唯一：重复 (owner_scope, node_fingerprint) 被拒。"""
    db_path = tmp_path / "unique.db"
    shutil.copy(head_db, db_path)

    flags = _index_unique_flags(db_path, TABLE)
    assert flags.get(INDEX_NAME) is True, (
        f"upgrade head 后 {INDEX_NAME} 缺失或非唯一；实际: {flags}"
    )

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(_INSERT, ("u:x", "fp1", "ref1", "s1", "o1", "2026-10-20 00:00:00"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(_INSERT, ("u:x", "fp1", "ref2", "s1", "o1", "2026-10-20 00:00:01"))
        # 同 owner 不同指纹、不同 owner 同指纹都不受约束
        conn.execute(_INSERT, ("u:x", "fp2", "ref3", "s1", "o1", "2026-10-20 00:00:02"))
        conn.execute(_INSERT, ("u:y", "fp1", "ref4", "s2", "o1", "2026-10-20 00:00:03"))
        conn.commit()
        count = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    assert count == 3


def test_roundtrip_dedups_duplicates_and_restores_uniqueness(head_db, tmp_path):
    """downgrade → 0029 非唯一形态；存量重复行下 re-upgrade dedup + 重建唯一。"""
    db_path = tmp_path / "roundtrip.db"
    shutil.copy(head_db, db_path)

    down = _alembic(db_path, "downgrade", PREV_HEAD)
    assert down.returncode == 0, f"downgrade 失败:\n{down.stdout}\n{down.stderr}"
    assert _index_unique_flags(db_path, TABLE).get(INDEX_NAME) is False, (
        "downgrade 未回到 0029 的非唯一形态"
    )

    # 非唯一窗口灌入 #1724 的并发漂移：同键两行（旧 + 新）
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(_INSERT, ("u:x", "fp1", "stale", "s1", "o1", "2026-10-19 00:00:00"))
        conn.execute(_INSERT, ("u:x", "fp1", "fresh", "s1", "o1", "2026-10-20 09:00:00"))
        conn.commit()

    up = _alembic(db_path, "upgrade", "head")
    assert up.returncode == 0, f"re-upgrade 失败:\n{up.stdout}\n{up.stderr}"
    assert _index_unique_flags(db_path, TABLE).get(INDEX_NAME) is True, (
        "re-upgrade 未重建唯一索引"
    )
    with closing(sqlite3.connect(db_path)) as conn:
        kept = conn.execute(
            f"SELECT result_ref FROM {TABLE} "
            "WHERE owner_scope='u:x' AND node_fingerprint='fp1'"
        ).fetchall()
    assert kept == [("fresh",)], f"dedup 必须保留最新行；实际: {kept}"


def test_migration_chains_onto_current_head():
    """迁移必须链到 0096 head（Revision id / down_revision 双断言）。"""
    source = (REPO_ROOT / "migrations" / "versions" / MIGRATION).read_text(encoding="utf-8")
    assert 'revision: str = "0097_gc_node_result_owner_fp_unique"' in source
    assert PREV_HEAD in source, "down_revision 必须指向当前 head 0096_harness_turn_journal"
