"""Data Lifecycle — 孤儿修订行清理 plan/execute（P4 补强 D2）。

NOT EXISTS 反连接计划 + 执行期新鲜复检（artifact 回归 → 跳过删除）。
sqlite 内存库直驱真实 ORM 模型。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.db_model  # noqa: F401 — ORM 注册
import app.models.project  # noqa: F401
from app.core.database import Base
from app.services.data_lifecycle.quota import (
    execute_orphan_revision_cleanup,
    plan_orphan_revision_cleanup,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _mk_artifact(db, project_id: str = "p-orphan") -> str:
    from app.models.project import Artifact

    row = Artifact(id=str(uuid.uuid4()), project_id=project_id,
                   name="a", artifact_type="vector")
    db.add(row)
    db.commit()
    return row.id


def _mk_revision(db, artifact_id) -> str:
    from app.models.project import ArtifactRevision

    row = ArtifactRevision(id=str(uuid.uuid4()), artifact_id=artifact_id,
                           revision_no=1,
                           content_sha256=uuid.uuid4().hex,
                           content_location="blob/x")
    db.add(row)
    db.commit()
    return row.id


def test_plan_finds_revisions_whose_artifact_vanished(db) -> None:
    aid = _mk_artifact(db)
    kept = _mk_revision(db, aid)
    orphan = _mk_revision(db, str(uuid.uuid4()))  # artifact 行不存在
    plan = plan_orphan_revision_cleanup(db)
    ids = set(plan["candidate_revision_ids"])
    assert orphan in ids
    assert kept not in ids
    assert plan["candidate_count"] == len(ids)


def test_plan_respects_limit(db) -> None:
    for _ in range(5):
        _mk_revision(db, str(uuid.uuid4()))
    plan = plan_orphan_revision_cleanup(db, limit=2)
    assert plan["candidate_count"] <= 2
    assert plan["limit"] == 2


def test_execute_deletes_only_still_orphan_rows(db) -> None:
    aid = _mk_artifact(db)
    orphan = _mk_revision(db, str(uuid.uuid4()))
    plan = plan_orphan_revision_cleanup(db)
    result = execute_orphan_revision_cleanup(plan, db=db)
    assert result["deleted_count"] == 1
    assert orphan in result["deleted_revision_ids"]


def test_execute_rechecks_freshness_before_delete(db) -> None:
    # plan 后 artifact 行回归 → 执行期复检跳过（不再孤儿）。
    aid = _mk_artifact(db)
    rev = _mk_revision(db, aid)
    plan = plan_orphan_revision_cleanup(db, limit=0)  # 空 plan 不会用到
    # 人为构造 stale plan：把 kept 修订放进候选（模拟 plan 后 artifact 刚被删又被建）。
    stale_plan = {"candidate_revision_ids": [rev]}
    result = execute_orphan_revision_cleanup(stale_plan, db=db)
    assert result["deleted_count"] == 0


def test_execute_empty_plan_is_noop(db) -> None:
    assert execute_orphan_revision_cleanup({}, db=db) == {
        "deleted_revision_ids": [], "deleted_count": 0}
