"""P2 波次 `migrations/versions` 修复的回归守卫。

覆盖两类缺陷：
  A. create_all-coexistence 幂等（SQLite 分支缺 IF NOT EXISTS / inspect / if_exists
     守卫）—— init_db() 的 ``Base.metadata.create_all`` 已按模型建出同名索引/列，
     无守卫的 upgrade 会在其上 duplicate column/index 或 drop 时报 no such index。
     守卫形态：在 create_all 自举的库上 stamp 到目标迁移的 down_revision，再 upgrade
     到目标 —— 未修复代码必崩，修复后跳过/幂等。
  B. fail-closed 内省（0057/0058 ``_columns_of`` 表缺失返回空集 → 静默跳过增列）
     与表达式索引反射 TypeError（0106 ``_index_columns`` join None）。
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: create_all 前必须注册的模型模块（覆盖本任务触碰的表/索引/列）。
_MODEL_MODULES = (
    "app.models.db_model",
    "app.models.project",
    "app.models.upload",
    "app.models.data_fabric",
    "app.models.data_quality",
    "app.models.data_lifecycle",
    "app.models.knowledge_base",
    "app.models.report",
    "app.models.template_version",
    "app.models.cartography_quality",
    "app.models.ads_fabric",
    "app.models.gis_context",
    "app.models.harness_journal",
    "app.models.coordinate_operations",
)


def _create_all_db(db_url: str) -> None:
    from sqlalchemy import create_engine

    from app.core.database import Base

    for name in _MODEL_MODULES:
        importlib.import_module(name)
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


def _cfg(db_url: str):
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        }


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


# ── A1. create_index / ADD COLUMN 守卫（模型已声明同名索引/列）────────────────

@pytest.mark.parametrize(
    "target,down,table,artifact",
    [
        ("0012_add_composite_indexes_pd_wr", "0011_enterprise_geospatial_data_fabric",
         "project_datasets", "idx_project_dataset_pid_created"),
        ("0014_workflow_provenance_revisions", "0013_unified_durable_job_runtime",
         "project_datasets", "detached_at"),
        ("0015_uploads_session_upload_index", "0014_workflow_provenance_revisions",
         "uploads", "ix_uploads_session_time"),
        ("0017_close_model_migration_drift", "0016_knowledge_documents_owner",
         "analysis_tasks", "idx_task_owner_token"),
        ("0020_drop_data_fabric_prefix_indexes", "0019_drop_leftover_duplicate_indexes",
         "knowledge_documents", "ix_knowledge_documents_creator_id"),
        ("0103_workflow_run_session", "0102_owner_token_hashes",
         "workflow_runs", "session_id"),
        ("b2c3d4e5f6a7", "6ef479051297",
         "messages", "idx_message_conversation_created"),
    ],
)
def test_upgrade_is_idempotent_on_create_all_db(tmp_path, monkeypatch, target, down, table, artifact):
    """create_all 自举库上 stamp→upgrade 不得因 duplicate column/index 崩。

    未修复代码在 SQLite 分支裸 create_index/ADD COLUMN，遇到 create_all 已建的
    同名对象即崩溃；修复后 inspect 守卫跳过。
    """
    from alembic import command

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    cfg = _cfg(db_url)
    command.stamp(cfg, down)
    command.upgrade(cfg, target)  # 未修复代码在此崩溃
    # 索引或列由 create_all 建出、迁移幂等保留
    if artifact.startswith("idx_") or artifact.startswith("ix_"):
        assert artifact in _indexes(db_path, table), f"{table} 缺索引 {artifact}"
    else:
        assert artifact in _columns(db_path, table), f"{table} 缺列 {artifact}"


# ── A2. drop_index if_exists 守卫（迁移侧索引 create_all 从不建）───────────────

@pytest.mark.parametrize(
    "target,down",
    [
        ("0018_dedupe_duplicate_indexes", "0017_close_model_migration_drift"),
        ("0019_drop_leftover_duplicate_indexes", "0018_dedupe_duplicate_indexes"),
    ],
)
def test_drop_index_if_exists_on_create_all_db(tmp_path, monkeypatch, target, down):
    """create_all 库不建 ix_reports_* / idx_template_* 等迁移侧索引，drop 无
    if_exists 即 no such index；修复后幂等 no-op。"""
    from alembic import command

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    cfg = _cfg(db_url)
    command.stamp(cfg, down)
    command.upgrade(cfg, target)  # 未修复代码在此 no such index 崩溃


# ── A3. create_table 守卫（0032）─────────────────────────────────────────────

def test_0032_create_table_guard_on_create_all_db(tmp_path, monkeypatch):
    """workflow_resume_anchors 由模型声明，create_all 库上无守卫的 create_table
    即 duplicate table；修复后 has_table 早退。"""
    from alembic import command

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    cfg = _cfg(db_url)
    command.stamp(cfg, "0031_revision_indexes")
    command.upgrade(cfg, "0032_harness_v5_resume_anchors")  # 未修复代码在此崩溃
    assert "workflow_resume_anchors" in _tables(db_path)


# ── B1. _columns_of fail-closed（0057 / 0058）────────────────────────────────

@pytest.mark.parametrize("module_name", [
    "migrations.versions.0057_cartography_intent_learning",
    "migrations.versions.0058_quality_cost_and_wave",
])
def test_columns_of_raises_on_missing_table(tmp_path, module_name):
    """表缺失必须 raise（fail-closed）而非返回空集 —— 否则调用方 `if cols and ...`
    静默跳过增列，schema 与模型静默漂移。未修复代码返回空集（不 raise）。"""
    from sqlalchemy import create_engine

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = importlib.import_module(module_name)
    engine = create_engine(f"sqlite:///{tmp_path}/cols.db")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError):
                module._columns_of("no_such_table_xyz")


# ── B2. 0106 _index_columns 对表达式索引不得 TypeError ───────────────────────

def test_0106_index_columns_survives_expression_index(tmp_path, monkeypatch):
    """PG 反射里 COALESCE 表达式索引的 column_names 含 None（create_all 按模型建
    的形态）—— _index_columns 直接 join 会 TypeError 令 upgrade 崩。修复后判为
    「非朴素形」返回 None，upgrade 幂等 no-op。"""
    from sqlalchemy import create_engine, inspect, text

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = importlib.import_module(
        "migrations.versions.0106_lifecycle_policy_org_unique"
    )

    db_url = f"sqlite:///{tmp_path}/lc106expr.db"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE lifecycle_policies ("
            " id VARCHAR(36) PRIMARY KEY, org_id INTEGER, name VARCHAR(64),"
            " kind VARCHAR(32), action VARCHAR(16), enabled BOOLEAN)"
        ))
    engine.dispose()

    real_inspect = inspect

    class _PgExprInspector:
        """PG 方言：表达式唯一索引 column_names 含 None。"""

        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def get_indexes(self, table):
            return [{
                "name": "uq_lifecycle_policy_org_name",
                "column_names": [None, "name"],
                "unique": True,
            }]

    monkeypatch.setattr(
        module.sa, "inspect",
        lambda bind: _PgExprInspector(real_inspect(bind)),
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()  # 未修复代码在此 TypeError
    finally:
        engine.dispose()


# ── C. g45 job_id → BigInteger（与 0109 同款，模型+迁移一致）─────────────────

def test_g45_job_id_is_bigint_in_model_and_migration():
    """coordinate_operation_runs.job_id 引用 analysis_tasks.id（BigInteger 主键）——
    32 位 Integer 在 PG 上超 2^31-1 溢出。模型与迁移都必须声明 BigInteger。"""
    from sqlalchemy import BigInteger

    from app.models.coordinate_operations import CoordinateOperationRun

    col = CoordinateOperationRun.__table__.columns["job_id"]
    assert isinstance(col.type, BigInteger), (
        f"模型 job_id 应为 BigInteger，实际 {type(col.type).__name__}"
    )
    src = (REPO_ROOT / "migrations" / "versions" / "g45_coordinate_operations.py").read_text(
        encoding="utf-8"
    )
    assert 'sa.BigInteger().with_variant(sa.Integer(), "sqlite")' in src, (
        "g45 迁移 job_id 应为 BigInteger().with_variant(Integer, sqlite)"
    )


# ── D. P3 波次：SQLite 分支幂等 / 0030 NULL 排序 ──────────────────────────────

def test_f123_sqlite_create_index_guard_on_create_all_db(tmp_path, monkeypatch):
    """f123 四个复合索引都是模型 index 名；create_all 库上 SQLite 分支裸
    create_index 即 duplicate index（PG 分支已有 IF NOT EXISTS）。"""
    from alembic import command

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    cfg = _cfg(db_url)
    command.stamp(cfg, "a1b2c3d4e5f6")
    command.upgrade(cfg, "f123456789ab")  # 未修复代码在此崩溃
    assert {
        "idx_layer_org_status",
        "idx_layer_org_category_status",
        "idx_task_org_status",
        "idx_task_org_type_status",
    } <= _indexes(db_path, "layers") | _indexes(db_path, "analysis_tasks")


def test_c1d2_sqlite_create_table_and_seed_guard_on_create_all_db(tmp_path, monkeypatch):
    """cartography_templates 由模型声明：create_all 库上 SQLite 分支无守卫
    create_table 即 duplicate table；表已存在且种子行已存在时 seed 亦主键冲突。"""
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.schemas.template_schema import SEED_TEMPLATES

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    seed_id = SEED_TEMPLATES[0]["id"]
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO cartography_templates "
                    "(id, kind, name, keywords, payload, is_builtin, version) "
                    "VALUES (:id, 'basemap', 'pre-existing', '[]', '{}', 0, 1)"
                ),
                {"id": seed_id},
            )
    finally:
        engine.dispose()
    cfg = _cfg(db_url)
    command.stamp(cfg, "b2c3d4e5f6a7")
    command.upgrade(cfg, "c1d2e3f4a5b6")  # 未修复代码在此崩溃
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM cartography_templates WHERE id=?", (seed_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM cartography_templates"
        ).fetchone()[0] == len(SEED_TEMPLATES)


def test_d3e4_sqlite_drop_column_guard_on_create_all_db(tmp_path, monkeypatch):
    """layers.properties_def 已从模型删除；create_all 库上 SQLite 分支
    batch drop_column 对不存在列抛 KeyError（PG 分支有 IF EXISTS）。"""
    from alembic import command

    db_path = tmp_path / "ca.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _create_all_db(db_url)
    cfg = _cfg(db_url)
    command.stamp(cfg, "c1d2e3f4a5b6")
    command.upgrade(cfg, "d3e4f5a6b7c8")  # 未修复代码在此崩溃
    assert "properties_def" not in _columns(db_path, "layers")


def test_0030_dedup_keeps_dated_row_over_null_created_at(tmp_path):
    """同组 created_at 为 NULL 与有值并存时，「保留最早」必须两端一致：
    显式 NULLS LAST 与 PG 默认同序；SQLite 默认 NULLS FIRST 会保留 NULL 行。"""
    from sqlalchemy import create_engine, text

    module = importlib.import_module(
        "migrations.versions.0030_revision_no_unique"
    )
    engine = create_engine(f"sqlite:///{tmp_path}/rev.db")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE artifact_revisions ("
                " id VARCHAR(36) PRIMARY KEY, artifact_id VARCHAR(36) NOT NULL,"
                " revision_no INTEGER NOT NULL, created_at DATETIME)"
            ))
            conn.execute(text(
                "INSERT INTO artifact_revisions "
                "(id, artifact_id, revision_no, created_at) VALUES "
                "('null-row', 'a1', 1, NULL),"
                "('dated-row', 'a1', 1, '2026-01-01 00:00:00')"
            ))
            module._dedup_revision_numbers(conn)
            rows = dict(conn.execute(text(
                "SELECT id, revision_no FROM artifact_revisions"
            )).fetchall())
        assert rows["dated-row"] == 1
        assert rows["null-row"] == 2
    finally:
        engine.dispose()
