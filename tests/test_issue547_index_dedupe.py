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