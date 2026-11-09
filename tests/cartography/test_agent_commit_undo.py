"""PR-D：Ctrl+Z 撤销一整笔 Agent 地图提交（撤销点 + 冲突处理）。

全部经真实 MapSpecLifecycleEngine（锁 + CAS + checkpoint）与 user mutation
HTTP 路由；不调用 LLM、不访问外部服务。
"""
import shutil
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import mapspec_mutations as _mod
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.mapspec import agent_undo
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchLayerStyleIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def sid():
    session_id = f"agent-undo-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)
    shutil.rmtree(BASE_STORAGE_DIR / session_id, ignore_errors=True)


@pytest.fixture
async def client(sid):
    app = FastAPI()
    app.dependency_overrides[require_owned_session] = lambda: Conversation(id=sid)
    app.include_router(_mod.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _layer(layer_id: str, color: str = "#0000ff"):
    return {"id": layer_id, "source": f"s-{layer_id}", "type": "circle",
            "paint": {"color": color}}


def _fc():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
         "properties": {}},
    ]}


async def _agent_upsert(engine, sid, layer_id, rev, color="#0000ff"):
    res = await engine.apply_mutation(
        sid, UpsertLayerIntent(layer=_layer(layer_id, color), source_data=_fc()),
        origin="agent", expected_revision=rev,
    )
    assert res.is_error is False, res.error_msg
    return res.mutation_revision


async def _layer_ids(sid):
    spec = await mapspec_store_instance.get_mapspec(sid)
    return [lyr["id"] for lyr in spec["layers"]]


async def _undo(client, sid, *, expected, base, agent, direction="undo"):
    return await client.post(
        f"/api/v1/chat/sessions/{sid}/mapspec/mutations",
        json={"intent": "undo_agent_commit", "expected_revision": expected,
              "base_revision": base, "agent_revision": agent, "direction": direction},
    )


async def _seed(sid):
    engine = MapSpecLifecycleEngine()
    init = await engine.apply_mutation(sid, InitProjectIntent())
    rev_a = await _agent_upsert(engine, sid, "A", init.mutation_revision)
    return engine, rev_a


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_agent_commit_records_undo_point_keyed_by_revision(sid):
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a)
    state = await session_data_manager.get_map_state(sid)
    points = {p["revision"]: p for p in agent_undo.undo_points_of(state)}
    assert set(points) >= {rev_a, rev_b}
    assert points[rev_b]["base_revision"] == rev_a
    assert points[rev_b]["pre_checkpoint_id"]
    assert points[rev_b]["post_fingerprint"].startswith("carto-sha256:")


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_undo_then_redo_whole_agent_commit(client, sid):
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a)
    assert await _layer_ids(sid) == ["A", "B"]

    undone = await _undo(client, sid, expected=rev_b, base=rev_a, agent=rev_b)
    assert undone.status_code == 200, undone.text
    assert await _layer_ids(sid) == ["A"]
    rev_u = undone.json()["mutation_revision"]
    assert rev_u == rev_b + 1
    runtime = await session_data_manager.get_map_state(sid)
    assert [lyr.get("id") for lyr in runtime.get("layers", [])] == ["A"]

    redone = await _undo(client, sid, expected=rev_u, base=rev_a, agent=rev_b,
                         direction="redo")
    assert redone.status_code == 200, redone.text
    assert await _layer_ids(sid) == ["A", "B"]

    # 重做之后可再次撤销（撤销点不因 undo/redo 消耗）
    again = await _undo(client, sid, expected=redone.json()["mutation_revision"],
                        base=rev_a, agent=rev_b)
    assert again.status_code == 200, again.text
    assert await _layer_ids(sid) == ["A"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_multi_commit_agent_turn_rolls_back_to_previous_checkpoint(client, sid):
    """一轮 Agent 产生多笔提交（前端只看到最终 revision）→ 整段回到轮前。"""
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a)
    rev_c = await _agent_upsert(engine, sid, "C", rev_b)
    res = await _undo(client, sid, expected=rev_c, base=rev_a, agent=rev_c)
    assert res.status_code == 200, res.text
    assert await _layer_ids(sid) == ["A"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_undo_refused_when_user_edited_after_agent_commit(client, sid):
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a, color="#0000ff")
    user = await engine.apply_mutation(
        sid, PatchLayerStyleIntent(layer_id="B", paint={"color": "#ff0000"}),
        origin="user", expected_revision=rev_b,
    )
    assert user.is_error is False

    res = await _undo(client, sid, expected=user.mutation_revision, base=rev_a, agent=rev_b)
    assert res.status_code == 400
    assert res.json()["detail"]["error_code"] == agent_undo.ERR_CONFLICT
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert [lyr["id"] for lyr in spec["layers"]] == ["A", "B"]
    assert next(lyr for lyr in spec["layers"] if lyr["id"] == "B")["paint"]["color"] == "#ff0000"

    # 用户先撤销自己的改动（同色回写）→ 状态回到 Agent 提交后 → 可撤销 Agent 提交
    back = await engine.apply_mutation(
        sid, PatchLayerStyleIntent(layer_id="B", paint={"color": "#0000ff"}),
        origin="user", expected_revision=user.mutation_revision,
    )
    assert back.is_error is False
    ok = await _undo(client, sid, expected=back.mutation_revision, base=rev_a, agent=rev_b)
    assert ok.status_code == 200, ok.text
    assert await _layer_ids(sid) == ["A"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_undo_refused_when_user_commit_interleaves_agent_segment(client, sid):
    engine, rev_a = await _seed(sid)
    user = await engine.apply_mutation(
        sid, PatchLayerStyleIntent(layer_id="A", paint={"color": "#ff0000"}),
        origin="user", expected_revision=rev_a,
    )
    rev_b = await _agent_upsert(engine, sid, "B", user.mutation_revision)
    res = await _undo(client, sid, expected=rev_b, base=rev_a - 1, agent=rev_b)
    assert res.status_code == 400
    assert res.json()["detail"]["error_code"] == agent_undo.ERR_INTERLEAVED
    assert await _layer_ids(sid) == ["A", "B"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_stale_expected_revision_is_superseded_not_applied(client, sid):
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a)
    await _agent_upsert(engine, sid, "C", rev_b)  # 并发 Agent 提交推进 revision
    res = await _undo(client, sid, expected=rev_b, base=rev_a, agent=rev_b)
    assert res.status_code == 409
    assert await _layer_ids(sid) == ["A", "B", "C"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_redo_without_undo_and_unknown_point_are_refused(client, sid):
    engine, rev_a = await _seed(sid)
    rev_b = await _agent_upsert(engine, sid, "B", rev_a)
    redo = await _undo(client, sid, expected=rev_b, base=rev_a, agent=rev_b, direction="redo")
    assert redo.status_code == 400
    assert redo.json()["detail"]["error_code"] == agent_undo.ERR_NOT_UNDONE
    unknown = await _undo(client, sid, expected=rev_b, base=rev_b + 5, agent=rev_b + 6)
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["error_code"] == agent_undo.ERR_EXPIRED


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_user_commits_do_not_record_undo_points(sid):
    engine, rev_a = await _seed(sid)
    before = agent_undo.undo_points_of(await session_data_manager.get_map_state(sid))
    await engine.apply_mutation(
        sid, PatchLayerStyleIntent(layer_id="A", paint={"color": "#ff0000"}),
        origin="user", expected_revision=rev_a,
    )
    after = agent_undo.undo_points_of(await session_data_manager.get_map_state(sid))
    assert after == before


def test_undo_point_list_is_bounded():
    state = {}
    for rev in range(1, agent_undo.AGENT_UNDO_LIMIT + 15):
        state.update(agent_undo.record_point_fields(
            state, revision=rev, base_revision=rev - 1,
            pre_checkpoint_id=f"ckpt_{rev}", post_fingerprint="fp"))
    points = agent_undo.undo_points_of(state)
    assert len(points) == agent_undo.AGENT_UNDO_LIMIT
    assert points[-1]["revision"] == agent_undo.AGENT_UNDO_LIMIT + 14
