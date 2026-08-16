"""Issue #429: uploads(session_id, upload_time) 复合索引的迁移守卫。

热路径 ``GET /api/v1/uploads``（list_uploads）查询
``WHERE session_id = ? ORDER BY upload_time DESC`` + COUNT —— 没有索引时每次
面板打开都是全表扫描 + 排序，代价随全局 uploads 行数线性增长。

守卫三件事：
  1. ORM 模型声明了复合索引（create_all 建出的新库直接带上）；
  2. Alembic upgrade head 后索引真实存在（存量库升级也带上）；
  3. downgrade → 0014 后索引消失、数据保留，re-upgrade 幂等（往返）。
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_NAME = "ix_uploads_session_time"


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


def _table_indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
            )
        }


def test_model_declares_session_upload_time_index():
    """ORM 模型必须声明 (session_id, upload_time) 复合索引。

    create_all 引导的新部署（本地 SQLite / 测试库）只会按模型建索引 ——
    模型缺声明则这些库永远扫全表。
    """
    from app.models.upload import UploadRecord

    indexes = {
        (ix.name, tuple(col.name for col in ix.columns))
        for ix in UploadRecord.__table__.indexes
    }
    assert (INDEX_NAME, ("session_id", "upload_time")) in indexes, (
        f"uploads 模型缺少复合索引 {INDEX_NAME}(session_id, upload_time)；实际: {sorted(indexes)}"
    )


def test_migration_head_creates_index(tmp_path):
    db_path = tmp_path / "uploads-index.db"
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
    assert INDEX_NAME in _table_indexes(db_path, "uploads"), (
        f"upgrade head 后 uploads 缺少索引 {INDEX_NAME}；"
        f"实际: {_table_indexes(db_path, 'uploads')}"
    )


def test_index_survives_roundtrip_and_preserves_rows(tmp_path):
    """downgrade → 0014 删索引且不丢数据；re-upgrade 幂等地重建。"""
    db_path = tmp_path / "uploads-roundtrip.db"
    assert _alembic(db_path, "upgrade", "head").returncode == 0

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO uploads (filename, original_name, file_type, format, file_size, session_id)"
            " VALUES ('u1/x.geojson', 'x.geojson', 'vector', 'geojson', 10, 'sess-a')"
        )
        conn.commit()

    down = _alembic(db_path, "downgrade", "0014_workflow_provenance_revisions")
    assert down.returncode == 0, f"downgrade 失败:\n{down.stdout}\n{down.stderr}"
    assert INDEX_NAME not in _table_indexes(db_path, "uploads"), (
        "downgrade 未移除索引"
    )

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
    assert count == 1, "downgrade 丢了 uploads 数据行"

    again = _alembic(db_path, "upgrade", "head")
    assert again.returncode == 0, f"re-upgrade 失败:\n{again.stdout}\n{again.stderr}"
    assert INDEX_NAME in _table_indexes(db_path, "uploads"), (
        "re-upgrade 未重建索引"
    )


def test_migration_chains_onto_0014_head():
    """迁移必须链到当时的 head（0014），否则 alembic 出多头。"""
    versions = (REPO_ROOT / "migrations" / "versions").glob("*.py")
    chained = [
        p
        for p in versions
        if INDEX_NAME in p.read_text(encoding="utf-8") and "op.create_index" in p.read_text(encoding="utf-8")
    ]
    assert chained, "未找到创建 uploads 索引的迁移文件"
    source = chained[0].read_text(encoding="utf-8")
    assert '0014_workflow_provenance_revisions' in source, (
        "新迁移的 down_revision 必须指向当前 head 0014_workflow_provenance_revisions"
    )
