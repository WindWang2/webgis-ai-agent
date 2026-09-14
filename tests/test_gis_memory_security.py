"""GIS Spatial Memory 安全/租户专项测试（R8）。

红线：
- 跨租户泄漏零容忍：org 谓词在 store/retrieval/projection 三层全部生效；
- 访问撤销 = 立即不可见（invalidate_for_scope）；
- sensitive 记忆永不进入模型投影；
- credential 永不落库（键剥除 + 值硬拒绝双防线）；
- session 等值边界：伪造他人 session_id 读不到记忆；
- 审计链：superseded/invalidated 保留可查，篡改不可。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.services.gis_memory import projection as pj
from app.services.gis_memory import queries as q
from app.services.gis_memory import retrieval as r
from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_RESOLVED_PLACE,
    KIND_USER_CARTO_PREF,
    SCOPE_PROJECT,
    SCOPE_SESSION,
    SCOPE_USER,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_USER_DECISION,
    MemoryEvidence,
    MemoryWriteRequest,
    RetrievedMemory,
    SpatialMemoryRecord,
)


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _override_db(factory):
    q.set_session_local_factory(factory)
    yield factory
    q.set_session_local_factory(None)


def _place(**kw) -> MemoryWriteRequest:
    base = dict(
        kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="sess-1",
        subject="成都市", value={"name": "成都市", "level": "city"},
        evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION),
        confidence=0.86, org_id="org-a",
    )
    base.update(kw)
    return MemoryWriteRequest(**base)


def test_cross_tenant_isolation_all_layers(factory):
    with factory() as db:
        s.record_memory(db, _place())
        db.commit()
    # store 层
    with factory() as db:
        assert s.get_active_memories(db, "org-b", SCOPE_SESSION, "sess-1") == []
        assert s.list_memories(db, org_id="org-b") == []
        assert s.memory_stats(db, org_id="org-b") == {}
        # retire 跨租户拒绝
        rows = s.list_memories(db, org_id="org-a")
        assert not s.retire_memory(db, org_id="org-b", memory_id=rows[0].id)
    # retrieval 层
    with factory() as db:
        hits = r.retrieve_memories(db, r.MemoryQueryContext(
            org_id="org-b", session_id="sess-1",
        ))
        assert hits == []
    # projection 层（narrow interface，租户不可见 = 空块）
    assert q.build_memory_projection_sync(q.MemoryProjectionInput(
        org_id="org-b", session_id="sess-1",
    )) == ""
    org_a_block = q.build_memory_projection_sync(
        q.MemoryProjectionInput(org_id="org-a", session_id="sess-1")
    )
    assert org_a_block.startswith("[GIS_MEMORY]")


def test_session_id_forgery_reads_nothing(factory):
    with factory() as db:
        s.record_memory(db, _place())
        db.commit()
    with factory() as db:
        assert r.retrieve_memories(db, r.MemoryQueryContext(
            org_id="org-a", session_id="sess-other",
        )) == []
        # scope_id 是等值边界：近似/前缀也不行
        assert s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-") == []


def test_access_revocation_invalidates_scope(factory):
    with factory() as db:
        s.record_memory(db, _place(scope=SCOPE_PROJECT, scope_id="proj-1"))
        s.record_memory(db, _place(scope=SCOPE_PROJECT, scope_id="proj-1",
                                   subject="重庆市"))
        db.commit()
        removed = s.invalidate_for_scope(
            db, org_id="org-a", scope=SCOPE_PROJECT, scope_id="proj-1"
        )
        db.commit()
        assert removed == 2
        assert s.get_active_memories(db, "org-a", SCOPE_PROJECT, "proj-1") == []
        # 审计保留
        audit = s.list_memories(db, org_id="org-a", scope=SCOPE_PROJECT)
        assert all(row.status == "invalidated" for row in audit)
        assert all(row.invalidation_rule == "scope_gone" for row in audit)


def test_sensitive_memory_never_projected(factory):
    with factory() as db:
        s.record_memory(db, _place(subject="内部位置", sensitive=True))
        db.commit()
    block = q.build_memory_projection_sync(q.MemoryProjectionInput(
        org_id="org-a", session_id="sess-1",
    ))
    assert "内部位置" not in block
    # 投影渲染函数本身的第二道防线（纵深防御：sensitive 行即使被传入也跳过）
    rec = SpatialMemoryRecord(
        id="m", kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="s",
        org_id="o", subject="敏感地名", value={"name": "敏感地名"},
        confidence=0.9, sensitive=True,
    )
    assert "敏感地名" not in pj.render_memory_block(
        [RetrievedMemory(record=rec, score=9.9, reasons=[])]
    )


def test_credentials_never_persisted(factory):
    with factory() as db:
        # 键剥除防线
        s.record_memory(db, _place(value={
            "name": "成都市", "api_key": "sk-secret", "token": "tok-1",
        }))
        db.commit()
        rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
        assert rows and "api_key" not in rows[0].value and "token" not in rows[0].value
    with factory() as db:
        # 值硬拒绝防线（独立 scope_id，避免与前段共享断言面）
        assert s.safe_record_memory(db, _place(
            scope_id="sess-cred", value={
                "note": "postgres://admin:sup3rsecret@db.internal/prod",
            },
        )) is False
        assert s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-cred") == []


def test_user_paths_redacted_in_refs(factory):
    with factory() as db:
        s.record_memory(db, _place(refs=["/home/alice/datasets/secret.parquet"]))
        db.commit()
        rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-1")
        assert all("/home/" not in ref for ref in rows[0].refs)


def test_user_scope_pref_isolated_between_users(factory):
    with factory() as db:
        s.record_memory(db, MemoryWriteRequest(
            kind=KIND_USER_CARTO_PREF, scope=SCOPE_USER, scope_id="u1",
            subject="basemap", value={"value": "dark"},
            evidence=MemoryEvidence(source=SOURCE_USER_DECISION),
            confidence=0.9, org_id="org-a", user_id="u1",
        ))
        db.commit()
        assert len(r.retrieve_memories(db, r.MemoryQueryContext(
            org_id="org-a", user_id="u1",
        ))) == 1
        assert r.retrieve_memories(db, r.MemoryQueryContext(
            org_id="org-a", user_id="u2",
        )) == []
        # org-B 同 user_id 也读不到（org + user 双谓词）
        assert r.retrieve_memories(db, r.MemoryQueryContext(
            org_id="org-b", user_id="u1",
        )) == []


def test_projection_never_raises_on_db_failure(factory):
    # DB 不可用 → 空串（fail-open 投影），绝不抬异常进 turn
    q.set_session_local_factory(lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    try:
        block = q.build_memory_projection_sync(q.MemoryProjectionInput(
            org_id="org-a", session_id="sess-1",
        ))
        assert block == ""
    finally:
        q.set_session_local_factory(factory)
