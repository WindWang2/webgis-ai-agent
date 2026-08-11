"""迁移 0013 的结构守卫（ADR-0052）。

为什么需要这个文件：所有 job 测试都用 ``Base.metadata.create_all`` 建表，**没有任何
测试执行过真正的 Alembic 迁移**。这正是典型的 false-green —— 审计里发现的
「PostgreSQL 上 `DATETIME` 类型不存在导致 upgrade 整体失败」就完全逃过了绿灯测试。

这里做三件事：
  1. 在真实 SQLite 上跑 upgrade head → 断言新列/约束/索引确实存在；
  2. 断言老数据行在 upgrade + downgrade 往返后仍然存在（规范 §39：不得要求删数据）；
  3. 对 PostgreSQL 分支做**静态**校验：确保 DDL 里不出现非 PG 类型名。
     （本地无 PG 实例，所以不做真实执行 —— 明确标注为静态检查，不谎称集成验证。）
"""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations" / "versions" / "0013_unified_durable_job_runtime.py"

#: 迁移必须新增的列（与 db_model.AnalysisTask 的 ADR-0052 字段一致）
EXPECTED_COLUMNS = {
    "job_kind",
    "display_name",
    "session_id",
    "owner_token",
    "project_id",
    "run_id",
    "turn_id",
    "tool_call_id",
    "agent_task_id",
    "agent_step_id",
    "idempotency_key",
    "attempt",
    "worker_id",
    "cancel_requested_at",
    "heartbeat_at",
    "result_ref",
    "dispatch_spec",
}

EXPECTED_INDEXES = {
    "idx_task_session_created",
    "idx_task_creator_created",
    "idx_task_status_heartbeat",
    "idx_task_agent_task",
    "uq_analysis_tasks_idempotency_key",
}

#: 升级前就存在的索引，重建表时不能丢
PRESERVED_INDEXES = {"idx_task_status", "idx_task_org_status", "idx_task_org_type_status"}


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
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


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
            )
        }


def _table_sql(db_path: Path, table: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()
    return row[0] if row else ""


@pytest.fixture
def migrated_db(tmp_path):
    """真实执行 alembic upgrade head 的 SQLite 数据库。"""
    if shutil.which(sys.executable) is None:  # pragma: no cover
        pytest.skip("python executable unavailable")
    db_path = tmp_path / "migration.db"
    result = _alembic(db_path, "upgrade", "head")
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return db_path


def test_upgrade_adds_all_durable_job_columns(migrated_db):
    columns = _columns(migrated_db, "analysis_tasks")
    missing = EXPECTED_COLUMNS - columns
    assert not missing, f"迁移缺少列: {sorted(missing)}"


def test_upgrade_creates_new_indexes_and_preserves_old_ones(migrated_db):
    indexes = _indexes(migrated_db, "analysis_tasks")
    assert not EXPECTED_INDEXES - indexes, f"缺少新索引: {sorted(EXPECTED_INDEXES - indexes)}"
    # 重建表（SQLite 无法 ALTER 约束）时不能把升级前的索引弄丢
    assert not PRESERVED_INDEXES - indexes, (
        f"重建表丢失了原有索引: {sorted(PRESERVED_INDEXES - indexes)}"
    )


def test_upgrade_widens_status_check_and_relaxes_nullability(migrated_db):
    """cancelling/stale 必须被 CHECK 接受；org_id/creator_id 必须可为 NULL。"""
    with sqlite3.connect(migrated_db) as conn:
        for status in ("pending", "queued", "running", "cancelling", "completed", "failed", "cancelled", "stale"):
            conn.execute(
                "INSERT INTO analysis_tasks (org_id, creator_id, task_type, parameters, status, progress)"
                " VALUES (NULL, NULL, 't', '{}', ?, 0)",
                (status,),
            )
        conn.commit()

        # 未知状态仍必须被拒绝 —— CHECK 是放宽而不是取消
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analysis_tasks (org_id, task_type, parameters, status, progress)"
                " VALUES (NULL, 't', '{}', 'bogus_status', 0)"
            )
        # progress 上下界约束不能在重建中丢掉
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analysis_tasks (org_id, task_type, parameters, status, progress)"
                " VALUES (NULL, 't', '{}', 'running', 101)"
            )


def test_upgrade_makes_primary_key_autoincrement_on_sqlite(migrated_db):
    """BIGINT 主键在 SQLite 上不自增 —— durable job 是热路径，必须能自增插入。"""
    assert "id INTEGER NOT NULL" in _table_sql(migrated_db, "analysis_tasks")
    with sqlite3.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO analysis_tasks (org_id, task_type, parameters, status, progress)"
            " VALUES (NULL, 't', '{}', 'pending', 0)"
        )
        conn.commit()
        assert conn.execute("SELECT id FROM analysis_tasks").fetchone()[0] is not None


def test_downgrade_preserves_existing_rows(tmp_path):
    """规范 §39：迁移不得要求删除现有 task 数据。"""
    db_path = tmp_path / "roundtrip.db"
    assert _alembic(db_path, "upgrade", "head").returncode == 0

    with sqlite3.connect(db_path) as conn:
        for status in ("running", "cancelling", "stale", "completed"):
            conn.execute(
                "INSERT INTO analysis_tasks (org_id, task_type, parameters, status, progress)"
                " VALUES (NULL, 't', '{}', ?, 0)",
                (status,),
            )
        conn.commit()

    down = _alembic(db_path, "downgrade", "0012_add_composite_indexes_pd_wr")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"

    columns = _columns(db_path, "analysis_tasks")
    assert not (EXPECTED_COLUMNS & columns), "downgrade 未移除新列"

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT status FROM analysis_tasks ORDER BY id").fetchall()
    assert len(rows) == 4, "downgrade 丢了数据行"
    # cancelling/stale 在旧 CHECK 下不合法 —— 归一化为 failed 而不是删行
    statuses = [r[0] for r in rows]
    assert statuses.count("failed") == 2
    assert "running" in statuses and "completed" in statuses


def test_upgrade_is_reapplicable_after_downgrade(tmp_path):
    db_path = tmp_path / "reapply.db"
    assert _alembic(db_path, "upgrade", "head").returncode == 0
    assert _alembic(db_path, "downgrade", "0012_add_composite_indexes_pd_wr").returncode == 0
    again = _alembic(db_path, "upgrade", "head")
    assert again.returncode == 0, f"re-upgrade failed:\n{again.stdout}\n{again.stderr}"
    assert not EXPECTED_COLUMNS - _columns(db_path, "analysis_tasks")


def test_full_downgrade_to_base_succeeds(tmp_path):
    db_path = tmp_path / "tobase.db"
    assert _alembic(db_path, "upgrade", "head").returncode == 0
    result = _alembic(db_path, "downgrade", "base")
    assert result.returncode == 0, f"downgrade base failed:\n{result.stdout}\n{result.stderr}"


# ── PostgreSQL 分支的静态校验 ───────────────────────────────────────
# 本地没有 PG 实例，所以这里只做静态检查，**不**声称做了 PG 集成验证。


def test_postgres_branch_uses_valid_postgres_types():
    """PG 没有 DATETIME 类型 —— 直接把 SQLite 类型名拼进 DDL 会让 upgrade 整体失败。

    这个守卫存在的原因：审计发现过正是这个缺陷，而所有测试都走 create_all，
    完全没碰迁移，因此一路绿灯。
    """
    source = MIGRATION.read_text(encoding="utf-8")
    assert "_PG_TYPES" in source, "迁移必须显式做方言类型映射"
    assert '"DATETIME": "TIMESTAMP"' in source, "DATETIME 必须在 PG 分支映射为 TIMESTAMP"
    assert "_pg_type(type_key)" in source, "PG 的 ADD COLUMN 必须经过类型映射"

    # 拼接 DDL 的那一行不得直接使用未映射的 type_key
    assert "ADD COLUMN IF NOT EXISTS {name} {type_key}" not in source


def test_postgres_branch_ddl_is_idempotent():
    """PG DDL 必须可重复执行（IF NOT EXISTS / IF EXISTS）。"""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in source
    # 索引 DDL 是 f-string 拼的（普通索引 / UNIQUE 共用一条语句）
    assert "IF NOT EXISTS {index_name}" in source, "索引创建必须是 IF NOT EXISTS"
    assert "DROP INDEX IF EXISTS" in source
    assert "DROP CONSTRAINT IF EXISTS ck_task_status" in source


def test_migration_declares_expected_revision_chain():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0013_unified_durable_job_runtime"' in source
    assert '"0012_add_composite_indexes_pd_wr"' in source
