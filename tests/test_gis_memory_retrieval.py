"""SpatialMemory 检索测试（R4）：谓词过滤、top-k 有界、理由、确定性。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.services.gis_memory import retrieval as r
from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_DATASET_SEMANTICS,
    KIND_PROVIDER_FAILURE,
    KIND_RESOLVED_PLACE,
    KIND_USER_CARTO_PREF,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    SOURCE_DATASET_PIN,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_TOOL_FAILURE,
    MemoryEvidence,
    MemoryWriteRequest,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def _req(**kw) -> MemoryWriteRequest:
    base = dict(
        kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="sess-1",
        subject="成都市",
        value={"name": "成都市", "level": "city"},
        evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION),
        confidence=0.86, org_id="org-a",
    )
    base.update(kw)
    return MemoryWriteRequest(**base)


def _ctx(**kw) -> r.MemoryQueryContext:
    base = dict(org_id="org-a", session_id="sess-1", now=_NOW)
    base.update(kw)
    return r.MemoryQueryContext(**base)


_NOW = datetime(2026, 9, 14, 12, 0, 0)


def test_retrieval_scope_union_and_ranking(db):
    s.record_memory(db, _req())
    s.record_memory(db, _req(scope=SCOPE_PROJECT, scope_id="proj-1", subject="重庆市"))
    db.commit()
    hits = r.retrieve_memories(db, _ctx(project_id="proj-1", subjects=("成都市",)))
    assert len(hits) == 2
    # 精确 subject 命中排第一，且理由披露
    assert hits[0].record.subject == "成都市"
    assert any(x.startswith("subject_exact") for x in hits[0].reasons)
    assert any(x.startswith("scope:") for x in hits[0].reasons)


def test_retrieval_topk_bounded_and_deterministic(db):
    for i in range(30):
        s.record_memory(db, _req(subject=f"place-{i:02d}", confidence=0.6))
    db.commit()
    hits = r.retrieve_memories(db, _ctx(limit=8))
    assert len(hits) == 8
    again = r.retrieve_memories(db, _ctx(limit=8))
    assert [h.record.id for h in hits] == [h.record.id for h in again]


def test_retrieval_excludes_cross_tenant_and_other_scope(db):
    s.record_memory(db, _req())
    s.record_memory(db, _req(org_id="org-b"))
    db.commit()
    hits = r.retrieve_memories(db, _ctx())
    assert len(hits) == 1 and hits[0].record.org_id == "org-a"
    # 项目记忆只在给了 project_id 时出现
    s.record_memory(db, _req(scope=SCOPE_PROJECT, scope_id="proj-9"))
    db.commit()
    assert len(r.retrieve_memories(db, _ctx())) == 1
    assert len(r.retrieve_memories(db, _ctx(project_id="proj-9"))) == 2


def test_retrieval_kind_filter_and_floor(db):
    s.record_memory(db, _req())
    s.record_memory(db, _req(
        kind=KIND_PROVIDER_FAILURE, subject="geocode.a",
        value={"failure_class": "timeout"},
        evidence=MemoryEvidence(source=SOURCE_TOOL_FAILURE),
        confidence=0.7,
    ))
    # 低置信历史行被读侧下限挡住
    s.record_memory(db, _req(subject="weak-place", confidence=0.4))
    db.commit()
    hits = r.retrieve_memories(db, _ctx(kinds=(KIND_PROVIDER_FAILURE,)))
    assert len(hits) == 1 and hits[0].record.kind == KIND_PROVIDER_FAILURE
    all_hits = r.retrieve_memories(db, _ctx())
    assert all(h.record.subject != "weak-place" for h in all_hits)


def test_retrieval_sensitive_excluded_by_default(db):
    s.record_memory(db, _req(subject="secret-place", sensitive=True))
    db.commit()
    assert len(r.retrieve_memories(db, _ctx())) == 0
    assert len(r.retrieve_memories(db, _ctx(include_sensitive=True))) == 1


def test_retrieval_dataset_version_compatibility(db):
    s.record_memory(db, MemoryWriteRequest(
        kind=KIND_DATASET_SEMANTICS, scope=SCOPE_PROJECT, scope_id="proj-1",
        subject="ds:hospitals",
        value={"dataset_key": "ds:hospitals", "version_token": "v1"},
        evidence=MemoryEvidence(source=SOURCE_DATASET_PIN),
        confidence=0.9, org_id="org-a", invalidation_rule="dataset_version",
    ))
    db.commit()
    ok_ctx = _ctx(
        project_id="proj-1",
        subjects=("ds:hospitals",),
        dataset_versions={"ds:hospitals": "v1"},
    )
    stale_ctx = _ctx(
        project_id="proj-1",
        subjects=("ds:hospitals",),
        dataset_versions={"ds:hospitals": "v2"},
    )
    assert len(r.retrieve_memories(db, ok_ctx)) == 1
    assert len(r.retrieve_memories(db, stale_ctx)) == 0


def test_latest_resolved_place_scope_fallback(db):
    # project 有、session 无 → 项目级兜底
    s.record_memory(db, _req(scope=SCOPE_PROJECT, scope_id="proj-1"))
    db.commit()
    hit = r.latest_resolved_place(
        db, org_id="org-a", project_id="proj-1"
    )
    assert hit is not None and hit.record.subject == "成都市"
    # session 有了 → session 优先（fresh + scope 权重）
    s.record_memory(db, _req(subject="高新区", value={"name": "高新区", "level": "district"}))
    db.commit()
    hit2 = r.latest_resolved_place(db, org_id="org-a", session_id="sess-1")
    assert hit2 is not None and hit2.record.scope == SCOPE_SESSION


def test_user_scope_only_when_identity_present(db):
    s.record_memory(db, _req(
        kind=KIND_USER_CARTO_PREF, scope=SCOPE_USER, scope_id="user-9",
        user_id="user-9", subject="basemap",
        evidence=MemoryEvidence(source="explicit_user_decision"),
    ))
    db.commit()
    assert len(r.retrieve_memories(db, _ctx(user_id="user-9"))) == 1
    assert len(r.retrieve_memories(db, _ctx(user_id="user-other"))) == 0
