"""V9 安全与多租户 P1 迁移测试（ADR-0139，0036–0038 org_id 落库）。

沿用 tests/integration/test_migration_lifecycle.py 的密闭模式
（DATABASE_URL 钉住 → alembic command 驱动）：

1. upgrade 到 V9 前头（c1e2f3a4b5c6，35+ 迁移全链）；
2. 以**存量脏数据**形态播种三子系统代表性行（有主 / 匿名 / 孤儿 /
   已带 org 四类）；
3. upgrade 到 0038 → 断言回填语义（owner→org / default 桶 / 继承链）、
   NOT NULL 与索引、控制面表保持 nullable；
4. downgrade 回 V9 前头 → 断言列/索引完整移除（runs 保留 nullable 列）；
5. 再 upgrade（up/down/up 纪律）。

资源：SQLite 单库，全程 < 60s；PG lane 仍由全链 lifecycle 测试覆盖。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
PRE_V9_HEAD = "c1e2f3a4b5c6"
POST_V9_HEAD = "0038_security_v9_lakehouse_org"


def _make_config(db_url: str):
    from alembic.config import Config

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


_PK_COUNTERS: dict[str, int] = {}


def _seed(conn, table: str, values: dict) -> None:
    """播种一行，自动补齐该表 NOT NULL 且无 server default 的列。

    填充值按 PRAGMA 类型推导（字符串 'x' / 数值 0 / JSON '{}' /
    DATETIME 固定时刻）；INTEGER PK 未显式给值时取表内递增计数。
    调用方必须显式提供带 CHECK 词表的列。
    """
    info = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    row = dict(values)
    for _cid, name, ctype, notnull, dflt, pk in info:
        if name in row or not notnull or dflt is not None:
            continue
        t = (ctype or "").upper()
        if pk:
            _PK_COUNTERS[table] = _PK_COUNTERS.get(table, 0) + 1
            row[name] = _PK_COUNTERS[table]
        elif "INT" in t or "BOOL" in t or "REAL" in t or "FLOA" in t or "DOUB" in t:
            row[name] = 0
        elif "JSON" in t:
            row[name] = "{}"
        elif "DATETIME" in t or "TIMESTAMP" in t or "DATE" in t:
            row[name] = "2026-01-01 00:00:00"
        else:
            row[name] = "x"
    cols = ", ".join(row)
    params = {k: v for k, v in row.items()}
    placeholders = ", ".join(f":{k}" for k in row)
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"), params)


def _seed_legacy_world(conn) -> None:
    """构造 V9 前的多租户脏数据：org 2 = acme，u1 有主，u2/c2 匿名。"""
    _seed(conn, "organizations", {"id": 2, "name": "Acme", "slug": "acme", "is_active": 1})
    _seed(conn, "users", {
        "id": "u1", "username": "u1", "email": "u1@x.test", "org_id": 2,
        "role": "editor", "is_active": 1, "token_version": 0,
    })
    _seed(conn, "users", {
        "id": "u2", "username": "u2", "email": "u2@x.test", "org_id": None,
        "role": "viewer", "is_active": 1, "token_version": 0,
    })
    _seed(conn, "projects", {"id": "p2", "name": "P2", "owner_id": "u1", "org_id": 2})
    _seed(conn, "projects", {"id": "p0", "name": "P0", "owner_id": "u1", "org_id": None})
    _seed(conn, "conversations", {"id": "c1", "title": "t", "user_id": "u1"})
    _seed(conn, "conversations", {"id": "c2", "title": "t", "user_id": None})

    # geocompute：r1 有主→2；r2 匿名→default；r3 已带 org→保留 '9'
    _seed(conn, "geocompute_runs", {
        "run_id": "r1", "owner_scope": "u:aaaa", "status": "queued",
        "plan_fingerprint": "f" * 32, "plan_snapshot": "{}",
        "creator_id": "u1", "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    })
    _seed(conn, "geocompute_runs", {
        "run_id": "r2", "owner_scope": "s:bbbb", "status": "queued",
        "plan_fingerprint": "f" * 32, "plan_snapshot": "{}",
        "creator_id": None, "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    })
    _seed(conn, "geocompute_runs", {
        "run_id": "r3", "owner_scope": "u:cccc", "status": "queued",
        "plan_fingerprint": "f" * 32, "plan_snapshot": "{}",
        "org_id": "9", "creator_id": None,
        "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00",
    })
    _seed(conn, "geocompute_run_events", {"id": 1, "run_id": "r1", "event": "x"})
    _seed(conn, "geocompute_run_events", {"id": 2, "run_id": "ghost", "event": "x"})
    _seed(conn, "geocompute_node_results", {
        "owner_scope": "u:aaaa", "node_fingerprint": "n" * 32,
        "result_ref": "ref1", "session_id": "c1",
    })
    _seed(conn, "geocompute_node_results", {
        "owner_scope": "s:bbbb", "node_fingerprint": "m" * 32,
        "result_ref": "ref2", "session_id": "ghost",
    })
    _seed(conn, "geocompute_run_evidence", {
        "run_id": "r1", "owner_scope": "u:aaaa", "status": "completed",
        "snapshot": "{}",
    })
    _seed(conn, "geocompute_artifacts", {
        "artifact_key": "a" * 64, "run_id": "r1", "kind": "payload",
    })

    # workflow：wi1 project p2→2；wi2 session c1→2；wi3 无主→default
    _seed(conn, "workflow_instances", {
        "instance_id": "wi1", "package_id": "pkg", "package_version": "1.0.0",
        "package_fingerprint": "f" * 64, "owner_scope": "u:aaaa",
        "project_id": "p2", "session_id": None,
    })
    _seed(conn, "workflow_instances", {
        "instance_id": "wi2", "package_id": "pkg", "package_version": "1.0.0",
        "package_fingerprint": "f" * 64, "owner_scope": "u:aaaa",
        "project_id": "p0", "session_id": "c1",
    })
    _seed(conn, "workflow_instances", {
        "instance_id": "wi3", "package_id": "pkg", "package_version": "1.0.0",
        "package_fingerprint": "f" * 64, "owner_scope": "s:bbbb",
        "project_id": None, "session_id": None,
    })
    _seed(conn, "workflow_instance_nodes", {"instance_id": "wi1", "node_id": "n1"})
    _seed(conn, "workflow_events", {"instance_id": "wi1", "kind": "transition"})
    _seed(conn, "workflow_node_reuse", {
        "owner_scope": "u:aaaa", "reuse_fingerprint": "r" * 32,
        "source_instance_id": "wi1", "artifact_ref": "ref:1",
        "artifact_session_id": "ghost",
    })
    _seed(conn, "workflow_node_reuse", {
        "owner_scope": "u:aaaa", "reuse_fingerprint": "q" * 32,
        "source_instance_id": "ghost", "artifact_ref": "ref:2",
        "artifact_session_id": "c1",
    })
    _seed(conn, "workflow_packages", {
        "package_id": "pkg", "version": "1.0.0", "schema_version": "1",
        "compiler_version": "1", "fingerprint": "f" * 64,
        "owner_scope": "u:aaaa", "project_id": "p2", "compiled_form": "{}",
    })
    _seed(conn, "workflow_packages", {
        "package_id": "pkg", "version": "2.0.0", "schema_version": "1",
        "compiler_version": "1", "fingerprint": "e" * 64,
        "owner_scope": "s:bbbb", "project_id": None, "compiled_form": "{}",
    })

    # lakehouse：ld1 project p2→2；ld2 session c1→2；ld3 session ghost→default
    _seed(conn, "lakehouse_datasets", {
        "dataset_id": "d" * 64, "owner_type": "project", "owner_id": "p2",
        "name": "ds1",
    })
    _seed(conn, "lakehouse_datasets", {
        "dataset_id": "e" * 64, "owner_type": "session", "owner_id": "c1",
        "name": "ds2",
    })
    _seed(conn, "lakehouse_datasets", {
        "dataset_id": "f" * 64, "owner_type": "session", "owner_id": "ghost",
        "name": "ds3",
    })
    _seed(conn, "lakehouse_dataset_versions", {
        "dataset_row_id": 1, "version_id": "v" * 64, "data_object_id": "b" * 64,
        "branch": "main",
    })
    _seed(conn, "lakehouse_dataset_refs", {
        "dataset_row_id": 1, "ref_type": "branch", "ref_name": "main",
        "version_id": "v" * 64,
    })
    _seed(conn, "lakehouse_catalog_items", {
        "object_id": "o" * 64, "owner_type": "project", "owner_id": "p2",
        "kind": "cube", "content_sha256": "c" * 64,
    })


def _org_of(conn, table: str, pk_col: str, pk: object) -> str | None:
    return conn.execute(
        text(f"SELECT org_id FROM {table} WHERE {pk_col} = :pk"), {"pk": pk}
    ).scalar_one()


def _notnull(conn, table: str, col: str = "org_id") -> int:
    for _cid, name, _t, notnull, _dflt, _pk in conn.execute(
        text(f"PRAGMA table_info({table})")
    ).fetchall():
        if name == col:
            return notnull
    raise AssertionError(f"{table}.{col} 缺失")


def _indexes(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(text(f"PRAGMA index_list({table})")).fetchall()}


def test_v9_org_backfill_up_down_up(tmp_path, monkeypatch):
    from alembic import command

    db_path = tmp_path / "v9mig.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    cfg = _make_config(db_url)

    command.upgrade(cfg, PRE_V9_HEAD)

    engine = __import__("sqlalchemy").create_engine(db_url)
    with engine.begin() as conn:
        _seed_legacy_world(conn)
    engine.dispose()

    command.upgrade(cfg, POST_V9_HEAD)

    engine = __import__("sqlalchemy").create_engine(db_url)
    with engine.connect() as conn:
        default = conn.execute(
            text("SELECT CAST(id AS VARCHAR) FROM organizations WHERE slug='default'")
        ).scalar_one()
        assert default is not None

        # ── 回填语义 ──
        assert _org_of(conn, "geocompute_runs", "run_id", "r1") == "2"
        assert _org_of(conn, "geocompute_runs", "run_id", "r2") == default
        assert _org_of(conn, "geocompute_runs", "run_id", "r3") == "9"  # 已带 org 不覆盖
        assert _org_of(conn, "geocompute_run_events", "id", 1) == "2"    # 随 run 继承
        assert _org_of(conn, "geocompute_run_events", "id", 2) == default  # 孤儿
        assert _org_of(conn, "geocompute_node_results", "result_ref", "ref1") == "2"
        assert _org_of(conn, "geocompute_node_results", "result_ref", "ref2") == default
        assert _org_of(conn, "geocompute_run_evidence", "run_id", "r1") == "2"
        assert _org_of(conn, "geocompute_artifacts", "artifact_key", "a" * 64) == "2"

        assert _org_of(conn, "workflow_instances", "instance_id", "wi1") == "2"
        assert _org_of(conn, "workflow_instances", "instance_id", "wi2") == "2"  # p0 无 org → 会话归属
        assert _org_of(conn, "workflow_instances", "instance_id", "wi3") == default
        assert _org_of(conn, "workflow_instance_nodes", "id", 1) is not None
        win_org = conn.execute(
            text("SELECT org_id FROM workflow_instance_nodes WHERE instance_id='wi1'")
        ).scalar_one()
        assert win_org == "2"
        assert conn.execute(
            text("SELECT org_id FROM workflow_events WHERE instance_id='wi1'")
        ).scalar_one() == "2"
        assert conn.execute(
            text("SELECT org_id FROM workflow_node_reuse WHERE artifact_ref='ref:1'")
        ).scalar_one() == "2"
        assert conn.execute(
            text("SELECT org_id FROM workflow_node_reuse WHERE artifact_ref='ref:2'")
        ).scalar_one() == "2"
        assert _org_of(conn, "workflow_packages", "version", "1.0.0") == "2"
        assert _org_of(conn, "workflow_packages", "version", "2.0.0") == default

        assert _org_of(conn, "lakehouse_datasets", "name", "ds1") == "2"
        assert _org_of(conn, "lakehouse_datasets", "name", "ds2") == "2"
        assert _org_of(conn, "lakehouse_datasets", "name", "ds3") == default
        assert conn.execute(text(
            "SELECT org_id FROM lakehouse_dataset_versions"
        )).scalar_one() == "2"
        assert conn.execute(text(
            "SELECT org_id FROM lakehouse_dataset_refs"
        )).scalar_one() == "2"
        assert _org_of(conn, "lakehouse_catalog_items", "kind", "cube") == "2"

        # ── NOT NULL：租户面硬约束，控制面保持 nullable ──
        for t in ("geocompute_runs", "geocompute_run_events", "geocompute_node_results",
                  "geocompute_run_evidence", "geocompute_artifacts",
                  "workflow_packages", "workflow_instances", "workflow_instance_nodes",
                  "workflow_events", "workflow_node_reuse",
                  "lakehouse_datasets", "lakehouse_dataset_versions",
                  "lakehouse_dataset_refs", "lakehouse_catalog_items"):
            assert _notnull(conn, t) == 1, f"{t}.org_id 应为 NOT NULL"
        for t in ("geocompute_workers", "geocompute_worker_cache",
                  "geocompute_resource_usage", "geocompute_task_quarantine",
                  "workflow_workers"):
            assert _notnull(conn, t) == 0, f"{t}.org_id 应保持 nullable（控制面）"

        # ── 索引 ──
        assert "idx_gc_run_org_created" in _indexes(conn, "geocompute_runs")
        assert "idx_gc_event_org_created" in _indexes(conn, "geocompute_run_events")
        assert "idx_wf_inst_org_created" in _indexes(conn, "workflow_instances")
        assert "idx_lh_ds_org_created" in _indexes(conn, "lakehouse_datasets")

        # 控制面表存量行 org_id 保持 NULL
        assert conn.execute(text(
            "SELECT org_id FROM geocompute_resource_usage LIMIT 1"
        )).scalar() is None
    engine.dispose()

    # ── downgrade：列移除（runs 保留 nullable 列）、索引清理 ──
    command.downgrade(cfg, PRE_V9_HEAD)

    engine = __import__("sqlalchemy").create_engine(db_url)
    with engine.connect() as conn:
        for t in ("geocompute_run_events", "geocompute_node_results",
                  "geocompute_run_evidence", "geocompute_artifacts",
                  "workflow_packages", "workflow_instances",
                  "workflow_instance_nodes", "workflow_events",
                  "workflow_node_reuse", "lakehouse_datasets",
                  "lakehouse_dataset_versions", "lakehouse_dataset_refs",
                  "lakehouse_catalog_items", "geocompute_workers",
                  "workflow_workers"):
            names = {r[1] for r in conn.execute(text(f"PRAGMA table_info({t})")).fetchall()}
            assert "org_id" not in names, f"{t}.org_id 应已移除"
        # runs 回 0033 形态：列保留、可空
        assert "org_id" in {
            r[1] for r in conn.execute(text("PRAGMA table_info(geocompute_runs)")).fetchall()
        }
        assert _notnull(conn, "geocompute_runs") == 0
        assert "idx_gc_run_org_created" not in _indexes(conn, "geocompute_runs")
    engine.dispose()

    # ── up/down/up 纪律：空库再升一次必须幂等可过 ──
    command.upgrade(cfg, POST_V9_HEAD)
