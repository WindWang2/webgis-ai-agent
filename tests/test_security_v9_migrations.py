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

import contextlib
from pathlib import Path

import pytest
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
PRE_V9_HEAD = "c1e2f3a4b5c6"
POST_V9_HEAD = "0041_security_v9_refresh_families"


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

        # ── 0039–0041：配额 / 审计 / refresh 家族表 ──
        quota_cols = {r[1] for r in conn.execute(
            text("PRAGMA table_info(org_quotas)")).fetchall()}
        assert {"org_id", "max_storage_bytes", "max_concurrent_tasks",
                "rate_limit_per_min"} <= quota_cols
        audit_cols = {r[1] for r in conn.execute(
            text("PRAGMA table_info(audit_events)")).fetchall()}
        assert {"id", "org_id", "actor_id", "action", "target_type",
                "target_id", "detail", "trace_id", "created_at"} <= audit_cols
        audit_idx = _indexes(conn, "audit_events")
        assert {"idx_audit_org_created", "idx_audit_actor_created",
                "idx_audit_action"} <= audit_idx
        fam_cols = {r[1] for r in conn.execute(
            text("PRAGMA table_info(refresh_token_families)")).fetchall()}
        assert {"user_id", "family_id", "current_jti",
                "reuse_detected", "expires_at"} <= fam_cols
        fam_idx = _indexes(conn, "refresh_token_families")
        assert {"idx_refresh_family_user", "idx_refresh_family_jti"} <= fam_idx
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


# ── 6. PG 分批回填的终止条件（#1306 分批 + 归属不可解析）────────────────
#
# _exec_update_batched 的 PG 路径按 ctid 分批 UPDATE，每批一个
# autocommit_block。当 SET 的子查询解析不出归属（creator/project 为空、
# 孤儿 id、归属人 org 为 NULL）时，UPDATE 把列设回原值仍计入 rowcount、
# 行仍满足 where → rc 永 >0 → 循环打到 1_000_000 硬帽（每批还是一个独立
# autocommit 事务，PG 升级事实挂死）。SQLite 路径全量一次、不触发，故用
# 「PG 形 SQL → SQLite 可执行」的绑定在本地复现该路径。


class _PgBackfillBind:
    """把 PG 形批量子查询翻译成 SQLite 可执行形式，模拟 PG 回填路径。

    ``ctid`` → ``rowid``；``IS DISTINCT FROM`` → ``IS NOT``（SQLite 的
    NULL 安全不等比较）。每批 execute 后即 commit —— 对应真实路径里每批
    都在自己的 ``autocommit_block`` 中落库（#1306）。
    """

    def __init__(self, engine, max_calls: int = 16):
        self._engine = engine
        self._conn = engine.connect()
        self._max_calls = max_calls
        self.calls = 0

    @property
    def dialect(self):
        return self._engine.dialect

    def execute(self, statement):
        self.calls += 1
        if self.calls > self._max_calls:
            raise AssertionError(
                f"回填循环未在真实进度为 0 时终止（已跑到第 {self.calls} 批；"
                "未修复时将打到 1_000_000 硬帽）"
            )
        sql = str(statement).replace("ctid", "rowid")
        sql = sql.replace("IS DISTINCT FROM", "IS NOT")
        result = self._conn.exec_driver_sql(sql)
        self._conn.commit()
        return result

    def dispose(self):
        self._conn.close()


class _FakeAutocommitContext:
    @contextlib.contextmanager
    def autocommit_block(self):
        yield


def _run_pg_backfill(module_name, engine, table, set_clause, where_clause,
                     monkeypatch):
    """在「假装是 PG」的绑定上跑某个 V9 迁移的分批回填 helper。"""
    import importlib

    from alembic import op

    module = importlib.import_module(f"migrations.versions.{module_name}")
    bind = _PgBackfillBind(engine)
    monkeypatch.setattr(module, "_is_sqlite", lambda: False)
    monkeypatch.setattr(op, "get_bind", lambda: bind)
    monkeypatch.setattr(op, "get_context", lambda: _FakeAutocommitContext())
    try:
        module._exec_update_batched(table, set_clause, where_clause)
    finally:
        bind.dispose()
    return bind


def _seed_org_resolution_world(engine) -> None:
    """users/projects + 三张待回填表：一行可解析 + 若干不可解析。"""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, org_id INTEGER)"
        ))
        conn.execute(text(
            "CREATE TABLE projects (id VARCHAR(64) PRIMARY KEY, org_id INTEGER)"
        ))
        conn.execute(text(
            "CREATE TABLE geocompute_runs (run_id VARCHAR(64) PRIMARY KEY,"
            " creator_id VARCHAR(64), org_id VARCHAR(255))"
        ))
        conn.execute(text(
            "CREATE TABLE workflow_instances (instance_id VARCHAR(64) PRIMARY KEY,"
            " project_id VARCHAR(64), org_id VARCHAR(255))"
        ))
        conn.execute(text(
            "CREATE TABLE lakehouse_datasets (dataset_id VARCHAR(64) PRIMARY KEY,"
            " owner_type VARCHAR(16), owner_id VARCHAR(64), org_id VARCHAR(255))"
        ))
        conn.execute(text("INSERT INTO users VALUES ('u1', 2), ('u9', NULL)"))
        conn.execute(text("INSERT INTO projects VALUES ('p2', 2), ('p0', NULL)"))
        # 可解析（r1/wi1/ld1）+ 三类不可解析：归属 id 为空 / 孤儿 / 归属人无 org
        conn.execute(text(
            "INSERT INTO geocompute_runs VALUES"
            " ('r1','u1',NULL),('r2',NULL,NULL),('r3','ghost',NULL),('r4','u9',NULL)"
        ))
        conn.execute(text(
            "INSERT INTO workflow_instances VALUES"
            " ('wi1','p2',NULL),('wi2','p0',NULL),('wi3',NULL,NULL),('wi4','ghost',NULL)"
        ))
        conn.execute(text(
            "INSERT INTO lakehouse_datasets VALUES"
            " ('ld1','project','p2',NULL),('ld2','project','p0',NULL),"
            " ('ld3','project','ghost',NULL)"
        ))


#: (迁移模块, 表, 主键列, SET 子句, 回填后应有的 org_id)
_PG_BACKFILL_CASES = (
    (
        "0036_security_v9_geocompute_org",
        "geocompute_runs",
        "run_id",
        "org_id = (SELECT CAST(u.org_id AS VARCHAR) FROM users u"
        " WHERE u.id = geocompute_runs.creator_id AND u.org_id IS NOT NULL)",
        {"r1": "2", "r2": None, "r3": None, "r4": None},
    ),
    (
        "0037_security_v9_workflow_runtime_org",
        "workflow_instances",
        "instance_id",
        "org_id = (SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        " WHERE p.id = workflow_instances.project_id AND p.org_id IS NOT NULL)",
        {"wi1": "2", "wi2": None, "wi3": None, "wi4": None},
    ),
    (
        "0038_security_v9_lakehouse_org",
        "lakehouse_datasets",
        "dataset_id",
        "org_id = (SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        " WHERE p.id = lakehouse_datasets.owner_id AND p.org_id IS NOT NULL)",
        {"ld1": "2", "ld2": None, "ld3": None},
    ),
)


@pytest.mark.parametrize(
    "module_name,table,pk,set_clause,expected",
    _PG_BACKFILL_CASES,
    ids=[case[0].split("_")[0] for case in _PG_BACKFILL_CASES],
)
def test_pg_backfill_terminates_when_owner_unresolvable(
    tmp_path, monkeypatch, module_name, table, pk, set_clause, expected
):
    """归属不可解析时，分批回填必须收敛而不是死循环打到硬帽。

    可解析行本轮写入；不可解析行保持 NULL 留给随后的兜底回填（default
    组织）—— 循环条件必须是「本轮真实改行数」。
    """
    import sqlalchemy

    engine = sqlalchemy.create_engine(
        f"sqlite:///{tmp_path / f'{module_name[:4]}.db'}"
    )
    _seed_org_resolution_world(engine)
    try:
        _run_pg_backfill(
            module_name, engine, table, set_clause, "org_id IS NULL", monkeypatch
        )
        with engine.connect() as conn:
            rows = dict(conn.execute(
                text(f"SELECT {pk}, org_id FROM {table}")
            ).fetchall())
        assert rows == expected
    finally:
        engine.dispose()


# ── 7. 0041 refresh 家族表的时钟源单一化 ────────────────────────────────


def test_0041_refresh_family_timestamps_have_no_server_default(
    tmp_path, monkeypatch
):
    """created_at/rotated_at 曾用 CURRENT_TIMESTAMP 服务端缺省 —— PG 上它落到
    TIMESTAMP WITHOUT TIME ZONE 是**会话本地墙钟**，而 expires_at 由应用侧 UTC
    生成：同一行两个时钟源，expires_at 比较整体偏移一个时区差，直接削弱本表
    承载的 reuse_detected 防盗用重放控制。仓库纪律（0022/0024/0026）：时钟列
    不设服务端缺省，一律由应用侧 UTC 写入。
    """
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{tmp_path / 'fam.db'}")
    module = importlib.import_module(
        "migrations.versions.0041_security_v9_refresh_families"
    )
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
    try:
        defaults = {
            c["name"]: c.get("default")
            for c in inspect(engine).get_columns("refresh_token_families")
        }
        for column in ("created_at", "rotated_at", "expires_at"):
            assert defaults.get(column) is None, (
                f"{column} 不得有服务端缺省 —— 时钟源必须单一（应用侧 UTC）"
            )
    finally:
        engine.dispose()
