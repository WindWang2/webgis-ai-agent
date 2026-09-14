"""GIS Spatial Memory 生产接线测试（R5/R6）：pending 缓冲、harvest 收割、
投影块、map_intent 复用兜底、dispatch 失败候选。

纪律红线：
- 工具热路径零 SQL（pending 缓冲）；org 由 harvest 烙印；
- 记忆链路任何失败 = 空串/零写入，绝不阻断 turn / 意图解析；
- 租户桥 fail-closed：无 _gis_memory_org 烙印 → 工具侧检索拒绝。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.lib.runtime import context as rt_ctx
from app.services.gis_memory import harvest as h
from app.services.gis_memory import pending as pd
from app.services.gis_memory import projection as pj
from app.services.gis_memory import queries as q
from app.services.gis_memory import retrieval as r
from app.services.gis_memory import store as s
from app.services.gis_memory.contract import (
    KIND_BOUNDARY_REF,
    KIND_DATASET_SEMANTICS,
    KIND_PROVIDER_FAILURE,
    KIND_RESOLVED_PLACE,
    SCOPE_SESSION,
    SOURCE_INTENT_RESOLUTION,
    SOURCE_TOOL_FAILURE,
    MemoryEvidence,
    MemoryWriteRequest,
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
def _isolated_pending():
    pd.pending_memory_buffer.discard("sess-w1")
    yield
    pd.pending_memory_buffer.discard("sess-w1")


# ── pending 缓冲 ──────────────────────────────────────────────────────


def test_pending_offer_drain_and_bounds():
    buf = pd.PendingMemoryBuffer()
    for i in range(pd.MAX_PENDING_PER_SESSION + 5):
        buf.offer("s", MemoryWriteRequest(
            kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="s",
            subject=f"p{i}", value={}, evidence=MemoryEvidence(
                source=SOURCE_INTENT_RESOLUTION),
            confidence=0.8, org_id="",
        ))
    assert buf.pending_count("s") == pd.MAX_PENDING_PER_SESSION
    drained = buf.drain("s")
    assert len(drained) == pd.MAX_PENDING_PER_SESSION
    assert drained[-1].subject == f"p{pd.MAX_PENDING_PER_SESSION + 4}"
    assert buf.pending_count("s") == 0


# ── 投影（R5）─────────────────────────────────────────────────────────


def test_projection_block_bounded_and_labelled():
    rec = s.SpatialMemoryRecord(
        id="m1", kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="s",
        org_id="o", subject="成都市",
        value={"name": "成都市", "level": "city", "bbox": [103.0, 30.1, 104.9, 31.4]},
        evidence={"source": "intent_resolution"}, confidence=0.86,
        last_validated_at="2026-09-14T10:00:00",
    )
    block = pj.render_memory_block([r.RetrievedMemory(record=rec, score=5.0, reasons=[])])
    assert block.startswith("[GIS_MEMORY]")
    assert "地理范围" in block and "intent_resolution" in block
    assert "先验" in block
    # 空命中 → 空串（不注入空块）
    assert pj.render_memory_block([]) == ""


def test_projection_budget_omission():
    records = []
    for i in range(40):
        records.append(r.RetrievedMemory(record=s.SpatialMemoryRecord(
            id=f"m{i}", kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION,
            scope_id="s", org_id="o", subject=f"地名{i:03d}较长的名字填充预算",
            value={"name": f"地名{i}"},
            evidence={"source": "intent_resolution"}, confidence=0.8,
        ), score=1.0, reasons=[]))
    block = pj.render_memory_block(records, char_budget=800)
    assert len(block) <= 800 + len("…（更多记忆已按预算省略）\n") + 2
    assert "省略" in block


# ── harvest（R6/R7 生产写位点）────────────────────────────────────────


class _FakeSessionData:
    def __init__(self):
        self._state = {}

    async def get_map_state(self, sid):
        return dict(self._state.get(sid, {}))

    async def set_map_state(self, sid, key, value, seq=None):
        self._state.setdefault(sid, {})[key] = value
        return True

    async def resolve_aliases(self, sid, strings):
        # registry 透明解引用入口：测试无别名登记，恒返回空映射
        return {}


class _FakeMapspecStore:
    def __init__(self, spec):
        self._spec = spec

    async def get_mapspec(self, sid):
        return self._spec


@pytest.mark.asyncio
async def test_harvest_drains_pending_and_stamps_org_bridge(factory, monkeypatch):
    h.set_session_local_factory(factory)
    fake_sd = _FakeSessionData()
    monkeypatch.setattr(
        "app.services.session_data.session_data_manager", fake_sd
    )
    monkeypatch.setattr(
        "app.services.mapspec.store.mapspec_store_instance",
        _FakeMapspecStore({}),
    )
    pd.pending_memory_buffer.offer("sess-w1", MemoryWriteRequest(
        kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="sess-w1",
        subject="成都市", value={"name": "成都市", "level": "city"},
        evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION),
        confidence=0.8, org_id="",  # 工具侧未烙印
    ))
    result = await h.harvest_spatial_memory(
        "sess-w1", None, org_id="org-a", user_id="u1"
    )
    assert result["written"] == 1
    # org 桥已烙印
    org, user = await q.read_memory_identity("sess-w1")
    assert org == "org-a" and user == "u1"
    # 记忆以 harvest 烙印的 org 落库
    engine = factory.kw["bind"]
    with factory() as db:
        rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-w1")
        assert len(rows) == 1 and rows[0].subject == "成都市"
        assert rows[0].org_id == "org-a"
    assert engine is not None


@pytest.mark.asyncio
async def test_harvest_profiles_from_mapspec(factory, monkeypatch):
    h.set_session_local_factory(factory)
    fake_sd = _FakeSessionData()
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_sd)
    spec = {
        "sources": {
            "s1": {
                "ref": "ds:hospitals",
                "profile": {
                    "featureCount": 120,
                    "geometryTypes": ["Point"],
                    "crs": "EPSG:4326",
                    "fields": {
                        "year": {"type": "int"},
                        "count": {"type": "float"},
                        "note": {"type": "str"},
                    },
                },
            }
        }
    }
    monkeypatch.setattr(
        "app.services.mapspec.store.mapspec_store_instance",
        _FakeMapspecStore(spec),
    )
    import app.services.chat.plan_orchestrator as _po

    monkeypatch.setattr(_po.plan_orchestrator, "get_plan", lambda sid: None)
    result = await h.harvest_spatial_memory(
        "sess-w1", "proj-1", org_id="org-a", user_id="u1"
    )
    # dataset_semantics + field_role + crs_resolution
    assert result["written"] == 3
    with factory() as db:
        rows = s.get_active_memories(db, "org-a", SCOPE_SESSION, "sess-w1")
        kinds = {row.kind for row in rows}
        assert kinds == {"dataset_semantics", "field_role", "crs_resolution"}
        sem = next(row for row in rows if row.kind == KIND_DATASET_SEMANTICS)
        assert sem.subject == "ds:hospitals"
        assert sem.value["crs"] == "EPSG:4326"
        roles = next(row for row in rows if row.kind == "field_role")
        assert roles.value["time"] == ["year"]
        assert roles.value["measure"] == ["count"]


@pytest.mark.asyncio
async def test_harvest_user_decisions_and_recipe_strategy(factory, monkeypatch):
    h.set_session_local_factory(factory)
    fake_sd = _FakeSessionData()
    fake_sd._state["sess-w1"] = {
        "_gis_provenance": [
            {"origin": "agent", "kind": "PatchLayerPresentationIntent",
             "target": "lyr-x", "detail": {"visible": False}},
            {"origin": "user", "kind": "PatchLayerPresentationIntent",
             "target": "lyr-1", "detail": {"visible": False}},
        ],
    }
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_sd)
    monkeypatch.setattr(
        "app.services.mapspec.store.mapspec_store_instance",
        _FakeMapspecStore({}),
    )

    result = await h.harvest_spatial_memory(
        "sess-w1", "proj-1", org_id="org-a", user_id="u1"
    )
    # review F1：recipe 成效的唯一存储是 ADR-0069 账本（harvest_project_memory
    # 在同位点写）——本表**不再**写 successful_strategy；只收 product_decision。
    assert result["written"] == 1
    with factory() as db:
        proj_rows = s.get_active_memories(
            db, "org-a", "project", "proj-1"
        )
        assert {row.subject for row in proj_rows} == {"layer:lyr-1"}
        assert proj_rows[0].value["decision"] == "hide_layer"
        assert proj_rows[0].evidence["source"] == "explicit_user_decision"


@pytest.mark.asyncio
async def test_harvest_sweeps_expired(factory, monkeypatch):
    h.set_session_local_factory(factory)
    fake_sd = _FakeSessionData()
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_sd)
    monkeypatch.setattr(
        "app.services.mapspec.store.mapspec_store_instance",
        _FakeMapspecStore({}),
    )
    # 直接落一条已过期的 provider_failure（ttl 1s，写完后时间前进不可控 →
    # 手动回填 expires_at）
    pd.pending_memory_buffer.offer("sess-w1", MemoryWriteRequest(
        kind=KIND_PROVIDER_FAILURE, scope=SCOPE_SESSION, scope_id="sess-w1",
        subject="geocode.a", value={"failure_class": "timeout"},
        evidence=MemoryEvidence(source=SOURCE_TOOL_FAILURE),
        confidence=0.85, org_id="", ttl_s=3600,
    ))
    await h.harvest_spatial_memory("sess-w1", None, org_id="org-a")
    with factory() as db:
        from app.models.spatial_memory import GISSpatialMemory

        row = db.query(GISSpatialMemory).filter(
            GISSpatialMemory.subject == "geocode.a"
        ).one()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        db.commit()
    result = await h.harvest_spatial_memory("sess-w1", None, org_id="org-a")
    assert result["swept"] >= 1


# ── 工具侧租户桥（fail-closed）────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_memory_identity_fail_closed():
    org, user = await q.read_memory_identity("")
    assert org == "" and user == ""
    org2, _ = await q.read_memory_identity("never-seen-session")
    assert org2 == ""  # 无烙印 = 拒绝检索


# ── map_intent 复用兜底（R6 集成）─────────────────────────────────────


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_map_intent_scope_fallback_from_memory(factory, monkeypatch, registry):
    from app.tools.registry import ToolRegistry as _TR  # noqa: F401

    h.set_session_local_factory(factory)
    with factory() as db:
        s.record_memory(db, MemoryWriteRequest(
            kind=KIND_RESOLVED_PLACE, scope=SCOPE_SESSION, scope_id="sess-w2",
            subject="成都市", value={"name": "成都市", "level": "city"},
            evidence=MemoryEvidence(source=SOURCE_INTENT_RESOLUTION),
            confidence=0.86, org_id="org-a",
        ))
        db.commit()

    fake_sd = _FakeSessionData()
    fake_sd._state["sess-w2"] = {"_gis_memory_org": "org-a", "_gis_memory_user": "u9"}
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_sd)

    with rt_ctx.bind_runtime_context(session_id="sess-w2", project_id=None):
        result = await registry.dispatch(
            "webgis_map_intent", {"query": "再看看医院"}, session_id="sess-w2"
        )
    text = str(result)
    assert "memory_scope" in text, (
        f"记忆兜底必须以 hint_applied 披露，实际：{text[:400]}"
    )
    assert "成都市" in text


@pytest.mark.asyncio
async def test_map_intent_fresh_resolution_wins_and_offers_candidate(
    factory, monkeypatch, registry
):
    h.set_session_local_factory(factory)
    fake_sd = _FakeSessionData()
    fake_sd._state["sess-w3"] = {"_gis_memory_org": "org-a"}
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_sd)

    with rt_ctx.bind_runtime_context(session_id="sess-w3", project_id=None):
        result = await registry.dispatch(
            "webgis_map_intent", {"query": "重庆市的医院分布"}, session_id="sess-w3"
        )
    text = str(result)
    assert "memory_scope" not in text  # fresh 解析，无兜底披露
    # fresh 解析的候选进入 pending（org 未烙印，等待 harvest）
    drained = pd.pending_memory_buffer.drain("sess-w3")
    kinds = {req.kind for req in drained}
    assert KIND_RESOLVED_PLACE in kinds and KIND_BOUNDARY_REF in kinds
    assert all(req.org_id == "" for req in drained)
