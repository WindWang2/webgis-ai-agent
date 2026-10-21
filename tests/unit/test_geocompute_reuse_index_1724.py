"""#1724 回归：复用索引「查最新」排序 + 竞态重复行守卫。

issue #1724（核心层契约缺口，本文件只覆盖 reuse_index.py 范围）：
- ``find_result`` docstring 承诺「查最新条目」却没有任何 ``order_by``
  （``.first()`` 语义不定 —— 唯一索引迁移修复前 / 竞态重复行下可能
  返回陈旧条目，错行被复用）；
- ``record_result`` 是 read-one → delete-one → insert，跨 worker 非原子，
  并发同键写入静默产生重复行（原子性由唯一索引担保，归核心契约修复员；
  本模块用 bulk delete 保证重写即刷新不残留重复行）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def idx_env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'reuse-index-1724.db'}")
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    # 模拟迁移 0029 的真实落库状态：索引按裸列名建成（无 unique=True，
    # 见 migrations/versions/0029_geocompute_v5_runtime.py）—— 重复行
    # 可落库，find_result/record_result 的守卫正是在该状态下被检验。
    # （db_model 声明的唯一索引归核心契约修复员补迁移，不在本文件范围。）
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_gc_node_result_owner_fp")
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index

    monkeypatch.setattr(reuse_index, "session_factory", factory)
    yield factory, eng
    eng.dispose()


def _naive_utc(days_ago: int) -> datetime:
    """naive UTC 时刻（与 DB 列 / reuse_index._utcnow 同一约定）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)


def _row(*, owner: str, fp: str, ref: str, created_at: datetime):
    from app.models.db_model import GeoComputeNodeResult

    return GeoComputeNodeResult(
        org_id="org-default", owner_scope=owner, node_fingerprint=fp,
        result_ref=ref, session_id="s1", upstream_fingerprints={},
        created_at=created_at,
    )


def test_find_result_returns_newest_row_among_duplicates(idx_env):
    """竞态重复行（唯一索引就位前的历史形状）→ 返回 created_at 最新条目
    （修复前 ``.first()`` 语义不定，可能复用陈旧/错行）。"""
    from app.services.geocompute import reuse_index

    factory, _ = idx_env
    with factory() as db:
        db.add(_row(owner="u:h1", fp="fp1", ref="ref:old",
                    created_at=_naive_utc(60)))
        db.add(_row(owner="u:h1", fp="fp1", ref="ref:new",
                    created_at=_naive_utc(30)))
        db.commit()

    entry = reuse_index.find_result("u:h1", "fp1")
    assert entry is not None
    assert entry["result_ref"] == "ref:new"
    assert entry["session_id"] == "s1"


def test_record_result_refresh_dedupes_existing_rows(idx_env):
    """重写即刷新：删除该键全部既有行（含竞态重复行）后插入 —— 索引不
    残留重复行（修复前只删读到的第一条）。"""
    from app.models.db_model import GeoComputeNodeResult
    from app.services.geocompute import reuse_index

    factory, _ = idx_env
    with factory() as db:
        db.add(_row(owner="u:h2", fp="fp2", ref="ref:dup1",
                    created_at=_naive_utc(60)))
        db.add(_row(owner="u:h2", fp="fp2", ref="ref:dup2",
                    created_at=_naive_utc(59)))
        db.commit()

    ok = reuse_index.record_result(
        owner_scope="u:h2", node_fingerprint="fp2",
        result_ref="ref:fresh", session_id="s1",
        upstream_fingerprints={"up": "fp-up"},
    )
    assert ok is True
    entry = reuse_index.find_result("u:h2", "fp2")
    assert entry is not None and entry["result_ref"] == "ref:fresh"
    assert entry["upstream_fingerprints"] == {"up": "fp-up"}
    with factory() as db:
        rows = db.query(GeoComputeNodeResult).filter(
            GeoComputeNodeResult.owner_scope == "u:h2",
            GeoComputeNodeResult.node_fingerprint == "fp2",
        ).all()
        assert len(rows) == 1


def test_find_result_owner_isolated(idx_env):
    """owner 域隔离不因排序/删除守卫削弱（SEC 锚）。"""
    from app.services.geocompute import reuse_index

    factory, _ = idx_env
    assert reuse_index.record_result(
        owner_scope="u:h3", node_fingerprint="fp3",
        result_ref="ref:mine", session_id="s1",
        upstream_fingerprints=None,
    ) is True
    assert reuse_index.find_result("u:other", "fp3") is None
    assert reuse_index.find_result("u:h3", "fp3")["result_ref"] == "ref:mine"
