"""#547 回归：重复索引去重 + 模型↔迁移约束漂移收敛。

修复前（小结，细节见 issue #547 findings）：
  - knowledge_chunks.document_id / data_sources.status 各有两份同列索引
    （index=True 生成的 ix_* + __table_args__ 遗留的 idx_*）—— 批量插入
    双倍写放大，且 create_all 与 alembic 两条建库路径都产生两份；
  - layers.creator_id 在 SQLite 链上仍是 NOT NULL（e46935 只放松了 PG），
    与模型 nullable=True 漂移 → SQLite 插入 creator_id=None 失败；
  - uq_datasource_org_name 唯一约束只在迁移（0011）存在，模型不知情 →
    autogenerate 反复想 drop 它，ORM 只报裸 IntegrityError。

本文件：模型元数据契约（无需 DB）+ SQLite 迁移链行为（alembic upgrade head
到 0018 后：索引收敛成单份、creator_id 可空、插入成功）。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.database import Base

import app.models.data_fabric  # noqa: F401  (registers DataSource)
import app.models.knowledge_base  # noqa: F401  (registers Chunk)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 1. 模型元数据契约：每 (表, 列) 只声明一份索引 ─────────────────────────


def test_models_declare_single_index_per_duplicated_column():
    kc = Base.metadata.tables["knowledge_chunks"]
    kc_idx = [ix for ix in kc.indexes if {c.name for c in ix.columns} == {"document_id"}]
    assert len(kc_idx) == 1, f"knowledge_chunks.document_id 索引应只有一份: {kc_idx}"
    assert kc_idx[0].name == "ix_knowledge_chunks_document_id"

    ds = Base.metadata.tables["data_sources"]
    ds_idx = [ix for ix in ds.indexes if {c.name for c in ix.columns} == {"status"}]
    assert len(ds_idx) == 1, f"data_sources.status 索引应只有一份: {ds_idx}"
    assert ds_idx[0].name == "ix_data_sources_status"


def test_models_declare_datasource_unique_constraint():
    """0011 两个方言都建了 uq_datasource_org_name —— 模型必须声明它，
    autogenerate 才会收敛（否则每次 autogenerate 都想 drop 该约束）。"""
    ds = Base.metadata.tables["data_sources"]
    constraint_names = {c.name for c in ds.constraints }
    assert "uq_datasource_org_name" in constraint_names, (
        f"DataSource 模型缺 uq_datasource_org_name 唯一约束: {constraint_names}"
    )


def test_reports_model_has_no_migration_side_duplicate_indexes():
    """sibling：reports 的迁移侧重复（ix_reports_session_id/ix_reports_status）
    由 0018 清除；模型本身只声明 idx_report_*（report.py 已有防重复注释）。"""
    rep = Base.metadata.tables["reports"]
    names = {ix.name for ix in rep.indexes}
    assert "ix_reports_session_id" not in names
    assert "ix_reports_status" not in names


# ── 2. SQLite 迁移链行为（alembic upgrade head → 0018）───────────────────


@pytest.fixture(scope="module")
def migrated_sqlite(tmp_path_factory):
    """在一次性 SQLite 上跑完整迁移链到 head（含 0018）。"""
    db_path = tmp_path_factory.mktemp("mig") / "mig.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "DATABASE_URL": f"sqlite:///{db_path}",
            "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
            "USE_REDIS": "false",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"upgrade head 失败:\n{result.stdout}\n{result.stderr}"
    return db_path


def test_sqlite_migrated_schema_converges_on_single_indexes(migrated_sqlite):
    """迁移后的 SQLite schema：document_id/status 各只剩一份规范索引；
    reports 只剩模型声明的 idx_report_* + ix_reports_share_code。"""
    engine = create_engine(f"sqlite:///{migrated_sqlite}")
    insp = inspect(engine)

    def cols(t):
        return [i["column_names"] for i in insp.get_indexes(t)]

    assert cols("knowledge_chunks").count(["document_id"]) == 1
    assert cols("data_sources").count(["status"]) == 1
    report_names = [i["name"] for i in insp.get_indexes("reports")]
    assert "ix_reports_session_id" not in report_names
    assert "ix_reports_status" not in report_names
    assert {"idx_report_session", "idx_report_status", "ix_reports_share_code"} <= set(report_names)
    engine.dispose()


def test_sqlite_migrated_layers_accept_null_creator_id(migrated_sqlite):
    """#547(4)：SQLite 链上 layers.creator_id 已放松为 nullable（与 PG 一致），
    creator_id=None 的 Layer 插入必须成功（修复前 NOT NULL → IntegrityError）。"""
    from app.models.db_model import Layer

    engine = create_engine(f"sqlite:///{migrated_sqlite}")
    insp = inspect(engine)
    layer_cols = {c["name"]: c for c in insp.get_columns("layers")}
    assert layer_cols["creator_id"]["nullable"] is True, (
        "layers.creator_id 在 SQLite 迁移链上仍为 NOT NULL（模型为 nullable=True），漂移未收敛"
    )

    with Session(engine) as s:
        # id 显式给：迁移建的 id 是 BIGINT PRIMARY KEY（非 rowid 别名），
        # SQLite 不会自动赋值 —— 与本测试验证的 creator_id 可空性无关。
        s.add(Layer(id=1, org_id=1, name="layer-null-creator", layer_type="vector", creator_id=None))
        s.commit()
        row = s.query(Layer).filter_by(name="layer-null-creator").one()
        assert row.creator_id is None
    engine.dispose()


# ── review B1 / #547：0018 的 PG downgrade 不得破坏 layers FK 语义 ───────
#
# 0018 的 PG upgrade 分支对 layers 不做任何修改（e46935 早已把 creator_id
# 放松为 nullable 并建立 ON DELETE SET NULL 的 layers_creator_id_fkey）。
# 修复前的 PG downgrade 会 DROP + 重建该 FK（不带 ON DELETE SET NULL）并
# ALTER COLUMN creator_id SET NOT NULL —— 一次 downgrade/upgrade 循环就
# 永久丢失"删除用户即置空 creator_id"的级联语义。


def test_0018_pg_downgrade_preserves_layer_fk_semantics_static():
    """静态守卫（无 DB，任何环境都跑）：0018 源文件里，PG downgrade 分支
    不得重新断言 layers.creator_id NOT NULL；若未来重建 layers_creator_id_fkey
    必须带 ON DELETE SET NULL。"""
    src = (REPO_ROOT / "migrations/versions/0018_dedupe_duplicate_indexes.py").read_text(encoding="utf-8")
    assert "ALTER COLUMN creator_id SET NOT NULL" not in src, (
        "0018 的 PG downgrade 不得重新断言 layers.creator_id NOT NULL（会回退 e46935）"
    )
    downgrade_section = src.split("def downgrade", 1)[1]
    if "ADD CONSTRAINT layers_creator_id_fkey" in downgrade_section:
        assert "ON DELETE SET NULL" in downgrade_section, (
            "0018 的 PG downgrade 若重建 layers_creator_id_fkey 必须带 ON DELETE SET NULL"
        )


def _pg_base_url():
    """可用则返回本地 PG 连接串，否则 None（test 自跳过）。"""
    import os as _os

    import sqlalchemy as _sa

    url = _os.environ.get("MIGRATION_DRIFT_DB_URL") or (
        "postgresql://test_user:test_pass@localhost:5432/test_db"
    )
    try:
        engine = _sa.create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(_sa.text("SELECT 1"))
        engine.dispose()
        return url
    except Exception:  # noqa: BLE001 — unreachable PG: skip the behavioral test
        return None


def test_0018_pg_downgrade_upgrade_cycle_preserves_layer_fk():
    """行为回归（PG 可用时）：scratch schema 上跑 upgrade head → downgrade
    0017 → upgrade head 的完整循环，每一步断言 layers.creator_id 的 FK 仍为
    ON DELETE SET NULL（pg_constraint.confdeltype='n'）且列可空。"""
    import os as _os
    import subprocess as _sp
    import sys as _sys

    import sqlalchemy as _sa

    base_url = _pg_base_url()
    if base_url is None:
        pytest.skip("PostgreSQL 不可用，跳过 0018 PG 循环行为测试")

    schema = f"b08_review_{_os.getpid()}"
    sep = "&" if "?" in base_url else "?"
    # 用裸 '=' 而不用 %3D：DATABASE_URL 会经 alembic 的 configparser
    # （默认 BasicInterpolation）落盘，'%' 会被当成插值字符报
    # "invalid interpolation syntax"。查询值里的第二个 '=' 对 SQLAlchemy 的
    # URL 解析是合法的（options=-csearch_path=<schema>），schema 名仅含
    # [a-zA-Z0-9_]，无需额外转义。
    scoped_url = f"{base_url}{sep}options=-csearch_path={schema}"
    env = {
        "PATH": _os.environ.get("PATH", "/usr/bin:/bin"),
        "DATABASE_URL": scoped_url,
        "JWT_SECRET_KEY": "test-secret-migration-32-chars-okay",
        "USE_REDIS": "false",
        "HOME": str(Path.home()),
    }
    engine = None
    try:
        # 准备独立 schema（不碰 test_db 的既有数据）
        admin = _sa.create_engine(base_url, connect_args={"connect_timeout": 3})
        with admin.connect() as conn:
            conn.execute(_sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(_sa.text(f'CREATE SCHEMA "{schema}"'))
            conn.commit()
        admin.dispose()

        def _run(*args: str) -> None:
            result = _sp.run(
                [_sys.executable, "-m", "alembic", *args],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            assert result.returncode == 0, f"alembic {args} 失败:\n{result.stdout}\n{result.stderr}"

        def _fk_and_nullable() -> tuple[str, bool]:
            nonlocal engine
            engine = _sa.create_engine(scoped_url)
            with engine.connect() as conn:
                deltype = conn.execute(
                    _sa.text(
                        "SELECT confdeltype FROM pg_constraint "
                        "WHERE conname = 'layers_creator_id_fkey' "
                        "AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = :s)"
                    ),
                    {"s": schema},
                ).scalar()
                nullable = conn.execute(
                    _sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'layers' AND column_name = 'creator_id'"
                    ),
                    {"s": schema},
                ).scalar()
            engine.dispose()
            engine = None
            return deltype, nullable == "YES"

        _run("upgrade", "head")
        assert _fk_and_nullable() == ("n", True), "head(0018) 后 FK 必须是 ON DELETE SET NULL 且列可空"

        _run("downgrade", "0017_close_model_migration_drift")
        assert _fk_and_nullable() == ("n", True), (
            "downgrade 0018→0017 后 FK 仍是 ON DELETE SET NULL 且列可空"
            "（修复前 downgrade 重建 FK 丢失 SET NULL 并 SET NOT NULL）"
        )

        _run("upgrade", "head")
        assert _fk_and_nullable() == ("n", True), "再 upgrade 0017→0018 后语义仍保持"
    finally:
        if engine is not None:
            engine.dispose()
        try:
            admin = _sa.create_engine(base_url, connect_args={"connect_timeout": 3})
            with admin.connect() as conn:
                conn.execute(_sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                conn.commit()
            admin.dispose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass