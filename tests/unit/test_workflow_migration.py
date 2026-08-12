"""Migration correctness for 0014_workflow_provenance_revisions (spec §31).

Verifies (in-process, no subprocess — robust to environments where
``sys.executable -m alembic`` is shimmed):
  * upgrade head creates workflow_revisions + every new column/index;
  * CHECK constraints on the altered tables are PRESERVED (native ALTER TABLE
    ADD/DROP COLUMN on SQLite 3.35+ does not rebuild the table);
  * downgrade removes them and is re-applicable (upgrade→downgrade→upgrade).
"""

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "wf_prov.db"
    url = f"sqlite:///{db_path}"
    # migrations/env.py reads DATABASE_URL at run time and OVERRIDES the config's
    # sqlalchemy.url (mirrors test_i6). Setting only the config option would let
    # CI's PostgreSQL DATABASE_URL win → the full migration chain would try to
    # create_table on a table that already exists in the shared CI DB. Force
    # SQLite by setting the env var the same way test_i6 does.
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, str(db_path)


def _columns(db_path, table):
    import sqlite3
    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    con.close()
    return set(cols)


def _tables(db_path):
    import sqlite3
    con = sqlite3.connect(db_path)
    tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return tabs


def _indexes(db_path, table):
    import sqlite3
    con = sqlite3.connect(db_path)
    idx = {r[1] for r in con.execute(f"PRAGMA index_list({table})")}
    con.close()
    return idx


def test_upgrade_adds_revisions_table_and_columns(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    assert "workflow_revisions" in _tables(db_path)
    for col in (
        "id", "workflow_id", "revision_no", "graph_spec",
        "inputs_schema", "graph_fingerprint", "created_by", "created_at",
    ):
        assert col in _columns(db_path, "workflow_revisions"), col

    assert "detached_at" in _columns(db_path, "project_datasets")
    assert "current_revision_id" in _columns(db_path, "workflows")
    for col in (
        "project_id", "workflow_revision_id", "graph_snapshot",
        "input_dataset_fingerprints", "completed_steps", "run_manifest",
        "run_fingerprint", "durable_job_id",
    ):
        assert col in _columns(db_path, "workflow_runs"), col
    assert "content_fingerprint" in _columns(db_path, "artifacts")
    for col in ("source_dataset_id", "source_dataset_fingerprint", "content_fingerprint"):
        assert col in _columns(db_path, "artifact_lineages"), col


def test_upgrade_creates_indexes(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    assert "idx_workflow_revision_wf_no" in _indexes(db_path, "workflow_revisions")
    assert "idx_workflow_run_project_created" in _indexes(db_path, "workflow_runs")
    assert "idx_project_dataset_pid_detached" in _indexes(db_path, "project_datasets")


def test_check_constraints_preserved_after_upgrade(alembic_cfg):
    """Native ADD COLUMN must not drop the existing CHECKs (regression guard)."""
    import sqlite3
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    # Need an org + project parent to satisfy FKs.
    con.execute("INSERT INTO organizations (id, name, slug) VALUES (1, 'o', 'o')")
    con.execute(
        "INSERT INTO projects (id, org_id, name, status) VALUES ('p1', 1, 'p', 'active')"
    )
    con.execute(
        "INSERT INTO workflows (id, project_id, name, version) "
        "VALUES ('w1', 'p1', 'w', 1)"
    )
    con.execute(
        "INSERT INTO workflow_runs (id, workflow_id, workflow_version, status) "
        "VALUES ('r1', 'w1', 1, 'running')"
    )
    # ck_workflow_run_status must still reject an invalid status.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO workflow_runs (id, workflow_id, workflow_version, status) "
            "VALUES ('r2', 'w1', 1, 'totally_bogus')"
        )
    con.close()


def test_downgrade_removes_additions_and_is_reapplicable(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0013_unified_durable_job_runtime")

    assert "workflow_revisions" not in _tables(db_path)
    assert "run_fingerprint" not in _columns(db_path, "workflow_runs")
    assert "detached_at" not in _columns(db_path, "project_datasets")

    # Re-apply: upgrade again must succeed (idempotent downgrade/upgrade cycle).
    command.upgrade(cfg, "head")
    assert "workflow_revisions" in _tables(db_path)
    assert "run_fingerprint" in _columns(db_path, "workflow_runs")


def test_models_match_migration_via_create_all(tmp_path):
    """create_all (the path unit tests use) must produce the same new columns."""
    from sqlalchemy import create_engine
    from app.core.database import Base
    import app.models  # noqa: F401  (register all models)

    db_path = tmp_path / "create_all.db"
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)

    assert "workflow_revisions" in _tables(str(db_path))
    assert "run_fingerprint" in _columns(str(db_path), "workflow_runs")
    assert "content_fingerprint" in _columns(str(db_path), "artifacts")
    assert "source_dataset_id" in _columns(str(db_path), "artifact_lineages")
    assert "detached_at" in _columns(str(db_path), "project_datasets")
