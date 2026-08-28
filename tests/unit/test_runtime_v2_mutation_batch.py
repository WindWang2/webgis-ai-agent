"""GIS Harness Runtime v2 — GISMutationBatch + user-wins guard extension tests.

Phase 2 + Phase 7 coverage:
- H1: finalize_display commits the show set AND hide set in ONE engine
  transaction (single lock/read/validation/revision bump) instead of one
  full mutation per layer.
- H2: finalize hide decisions are stamped presentation_owner="agent" —
  never laundered through the user mutation route.
- H3: agent UpsertLayerIntent cannot bypass a legacy ring user-hide.
- H5: AUTO_SAFE set_layer_visibility updates the cartographic_intent stamp.
"""
import uuid
from unittest.mock import patch

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
    mapspec_lifecycle_engine,
)
from app.services.gis_world_state import apply_gis_mutation, apply_gis_mutation_batch
from app.services.gis_world_state.provenance import append_provenance, ProvenanceEntry
from app.services.session_data import session_data_manager, MemorySessionStore


async def _seed_layers(session_id: str, layer_ids: list[str], *, boundary: str | None = None) -> None:
    """以 spec-backed 层填充会话（复用生产 upsert 通道）。"""
    engine = mapspec_lifecycle_engine
    for lid in layer_ids:
        await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer={
                    "id": lid,
                    "type": "circle",
                    "source": f"src-{lid}",
                    "paint": {"color": "#123456"},
                    **({"context_role": "boundary"} if lid == boundary else {}),
                },
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )


@pytest.fixture
async def clean_session():
    sid = f"v2-batch-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_finalize_batch_single_transaction_and_agent_origin(clean_session):
    """H1+H2：N 层收口 = 恰一次 commit、revision 恰 +1、owner=agent。"""
    await _seed_layers(clean_session, ["l-show", "l-hide-1", "l-hide-2", "l-b"], boundary="l-b")
    engine = mapspec_lifecycle_engine

    state0 = await session_data_manager.get_map_state(clean_session)
    rev0 = state0.get("_cartographic_mutation_revision", 0)

    batch = await apply_gis_mutation_batch(
        clean_session,
        [
            PatchLayerPresentationIntent(layer_id="l-show", visible=True),
            PatchLayerPresentationIntent(layer_id="l-hide-1", visible=False),
            PatchLayerPresentationIntent(layer_id="l-hide-2", visible=False),
        ],
        origin="agent",
        actor="finalize_display",
    )

    assert batch.committed is True
    assert batch.applied_count == 3
    # H1：整批恰一次 revision 递增（旧行为 = 每层一次）
    state1 = await session_data_manager.get_map_state(clean_session)
    assert state1["_cartographic_mutation_revision"] == rev0 + 1
    # H2：隐藏印记是 agent，不是 user
    by_id = {l["id"]: l for l in state1["mapspec"]["layers"]}
    for lid, expect_visible in [("l-show", True), ("l-hide-1", False), ("l-hide-2", False)]:
        intent = by_id[lid]["cartographic_intent"]
        assert intent["presentation_owner"] == "agent", (
            f"{lid} 的 owner 必须是 agent（H2：不得经 user 路由洗白）"
        )
        assert intent["expected_visible"] is expect_visible
        assert (by_id[lid]["layout"]["visibility"] == "visible") is expect_visible


@pytest.mark.asyncio
async def test_finalize_via_tool_uses_batch(clean_session):
    """finalize_display 工具面：单事务 + boundary 不隐藏 + not_found 上报。"""
    from app.tools.layer_manager import register_layer_management_tools
    from app.tools.registry import ToolRegistry

    await _seed_layers(clean_session, ["f-show", "f-mid", "f-b"], boundary="f-b")
    engine = mapspec_lifecycle_engine
    state0 = await session_data_manager.get_map_state(clean_session)
    rev0 = state0.get("_cartographic_mutation_revision", 0)

    registry = ToolRegistry()
    register_layer_management_tools(registry)
    out = await registry.dispatch(
        "finalize_display", {"show_refs": ["f-show"]}, session_id=clean_session,
    )
    assert out["success"] is True
    state1 = await session_data_manager.get_map_state(clean_session)
    assert state1["_cartographic_mutation_revision"] == rev0 + 1, (
        "整批（show + 隐藏集）必须恰一次 revision 递增"
    )
    by_id = {l["id"]: l for l in state1["mapspec"]["layers"]}
    assert by_id["f-show"]["cartographic_intent"]["expected_visible"] is True
    assert by_id["f-mid"]["cartographic_intent"]["expected_visible"] is False
    assert by_id["f-mid"]["cartographic_intent"]["presentation_owner"] == "agent"
    # boundary 语境层不参与收口隐藏
    assert by_id["f-b"].get("layout", {}).get("visibility", "visible") == "visible"
    assert out["final_display"]["hidden_patched"] == ["f-mid"]
    assert out["final_display"]["batch_committed"] is True


@pytest.mark.asyncio
async def test_batch_refuses_user_hidden_and_noop_skips_commit(clean_session):
    """user-owned 隐藏层在展示集 → refused；全 refused 批不落盘不递增。"""
    await _seed_layers(clean_session, ["u-hidden"])
    engine = mapspec_lifecycle_engine
    # 用户 durable 隐藏（spec 印记路径）
    await apply_gis_mutation(
        clean_session,
        PatchLayerPresentationIntent(layer_id="u-hidden", visible=False),
        origin="user",
        actor="test",
        expected_revision=1,
    )
    state0 = await session_data_manager.get_map_state(clean_session)
    rev0 = state0["_cartographic_mutation_revision"]

    batch = await apply_gis_mutation_batch(
        clean_session,
        [PatchLayerPresentationIntent(layer_id="u-hidden", visible=True)],
        origin="agent",
        actor="finalize_display",
    )
    assert batch.committed is False
    assert batch.refused_count == 1
    assert batch.outcomes[0].status == "refused"
    state1 = await session_data_manager.get_map_state(clean_session)
    assert state1["_cartographic_mutation_revision"] == rev0, (
        "no-op 批（全 refused）不得递增 revision"
    )
    by_id = {l["id"]: l for l in state1["mapspec"]["layers"]}
    assert by_id["u-hidden"]["cartographic_intent"]["presentation_owner"] == "user"
    assert by_id["u-hidden"]["cartographic_intent"]["expected_visible"] is False


@pytest.mark.asyncio
async def test_upsert_cannot_bypass_legacy_ring_user_hide(clean_session):
    """H3：legacy ring（无 spec 印记）下 agent upsert 不得翻回用户隐藏。"""
    await _seed_layers(clean_session, ["r-legacy"])
    # 直接改写 spec：抹掉 cartographic_intent 印记（模拟 #1070 之前的旧会话）
    spec = (await session_data_manager.get_map_state(clean_session))["mapspec"]
    for layer in spec["layers"]:
        layer.pop("cartographic_intent", None)
        layer["layout"] = {"visibility": "none"}  # 用户隐藏后的旧形态
    await session_data_manager.set_map_state_fields(clean_session, {"mapspec": spec})
    # ring 里只有用户 hide 决策
    await append_provenance(
        clean_session,
        ProvenanceEntry(
            seq=1, ts="2026-01-01T00:00:00Z", origin="user", actor="test",
            kind="PatchLayerPresentationIntent", target="r-legacy",
            revision=1, summary="visible=False", detail={"visible": False},
        ),
    )

    res = await apply_gis_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={
                "id": "r-legacy",
                "type": "circle",
                "source": "src-r-legacy",
                "paint": {"color": "#fff"},
                "layout": {"visibility": "visible"},
            },
            source_data={"type": "FeatureCollection", "features": []},
        ),
        origin="agent",
        actor="test",
    )
    assert res.is_error is True, "agent 不得借 upsert 翻回 ring 里的用户隐藏"
    assert "不覆盖用户显式操作" in (res.error_msg or "")

    # 新层（族不存在）不受守卫影响
    res2 = await apply_gis_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={
                "id": "r-new",
                "type": "circle",
                "source": "src-r-new",
                "paint": {"color": "#fff"},
            },
            source_data={"type": "FeatureCollection", "features": []},
        ),
        origin="agent",
        actor="test",
    )
    assert res2.is_error is False


def test_auto_safe_visibility_updates_intent_stamp():
    """H5：AUTO_SAFE set_layer_visibility 同步 expected_visible/owner 印记。"""
    from app.lib.cartography.quality_loop import _apply_repairs

    mapspec = {
        "version": "1.0",
        "view": {},
        "sources": {},
        "layers": [
            {
                "id": "x",
                "type": "circle",
                "source": "s",
                "visible": True,
                "layout": {"visibility": "none"},
                "cartographic_intent": {
                    "expected_visible": True,
                    "presentation_owner": "agent",
                },
            },
            {
                "id": "y",
                "type": "circle",
                "source": "s",
                "visible": True,
                "cartographic_intent": {
                    "expected_visible": False,
                    "presentation_owner": "user",
                },
            },
        ],
        "layout": {},
    }
    out = _apply_repairs(
        mapspec,
        [
            {"operation": "set_layer_visibility", "layer_id": "x", "visible": True},
            {"operation": "set_layer_visibility", "layer_id": "y", "visible": True},
        ],
    )
    by_id = {l["id"]: l for l in out["layers"]}
    assert by_id["x"]["cartographic_intent"]["expected_visible"] is True
    assert by_id["x"]["cartographic_intent"]["presentation_owner"] == "system"
    # user-owned 印记不被系统修复改写；expected_visible 同步（review 5/6-B9：
    # 该翻转必须被钉住 —— 用户决策值被翻转是危险方向）
    assert by_id["y"]["cartographic_intent"]["presentation_owner"] == "user"
    assert by_id["y"]["cartographic_intent"]["expected_visible"] is True


@pytest.mark.asyncio
async def test_patch_layer_style_intent_persists_paint(clean_session):
    """#1077：PatchLayerStyleIntent 把 paint 合并进 spec 层族（持久通道）。"""
    from app.services.mapspec.lifecycle_engine import PatchLayerStyleIntent

    await _seed_layers(clean_session, ["sty-1"])
    engine = mapspec_lifecycle_engine
    res = await engine.apply_mutation(
        clean_session,
        PatchLayerStyleIntent(layer_id="sty-1", paint={"color": "#00ff00"}),
        origin="user",
        expected_revision=1,
    )
    assert res.is_error is False
    state = await session_data_manager.get_map_state(clean_session)
    layer = next(l for l in state["mapspec"]["layers"] if l["id"] == "sty-1")
    assert layer["paint"]["color"] == "#00ff00"
    # 幂等合并：第二次 patch 保留第一次的键
    res2 = await engine.apply_mutation(
        clean_session,
        PatchLayerStyleIntent(layer_id="sty-1", paint={"radius": 12}),
        origin="user",
        expected_revision=2,
    )
    assert res2.is_error is False
    state2 = await session_data_manager.get_map_state(clean_session)
    layer2 = next(l for l in state2["mapspec"]["layers"] if l["id"] == "sty-1")
    assert layer2["paint"] == {"color": "#00ff00", "radius": 12}
    # 未知层拒绝
    res3 = await engine.apply_mutation(
        clean_session,
        PatchLayerStyleIntent(layer_id="missing", paint={"color": "#000"}),
        origin="user",
        expected_revision=3,
    )
    assert res3.is_error is True


# ── /goal §14 adversarial：锁降级/锁丢失/并发 user-vs-agent（review 5/6-B8）──

@pytest.mark.asyncio
async def test_engine_batch_fails_closed_on_degraded_lock(clean_session):
    """降级锁（Redis 配置但不可达）下引擎批必须拒绝 —— 两 worker 各持进程
    内锁并发提交的 lost-update 面在此关闭。"""
    import app.services.distributed_lock as dl
    from app.services.distributed_lock import (
        _InProcessLock, _ResilientSessionLock, LockDegradedError,
    )
    from unittest.mock import AsyncMock, MagicMock

    fake_client = MagicMock()
    fake_client.set = AsyncMock(side_effect=ConnectionError("redis down"))

    def degraded_factory(key, *args, **kwargs):
        return _ResilientSessionLock(
            fake_client, f"webgis:sessionlock:{key}", _InProcessLock(),
            fail_on_degraded=kwargs.get("fail_on_degraded", False),
        )

    real_lock = dl.session_lock_registry.lock
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dl.session_lock_registry, "lock", degraded_factory)
        with pytest.raises(LockDegradedError):
            await apply_gis_mutation_batch(
                clean_session,
                [PatchLayerPresentationIntent(layer_id="x", visible=True)],
                origin="agent",
                actor="test",
            )
    state = await session_data_manager.get_map_state(clean_session)
    assert state.get("_cartographic_mutation_revision", 0) == 0


@pytest.mark.asyncio
async def test_engine_batch_aborts_when_lock_lost_mid_batch(clean_session):
    """锁 TTL 丢失（commit 前置复检）→ 批中止、零持久化、结构化错误。"""
    await _seed_layers(clean_session, ["lost-1"])
    engine = mapspec_lifecycle_engine
    state0 = await session_data_manager.get_map_state(clean_session)
    rev0 = state0["_cartographic_mutation_revision"]

    class _LostLockProxy:
        """包装真实锁：进入后立刻标记 lost（模拟续期失败被发现的窗口）。"""
        def __init__(self, inner):
            self._inner = inner
            self.lost = True

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    import app.services.mapspec.lifecycle_engine as le
    real_lock = le.session_lock_registry.lock
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            le.session_lock_registry, "lock",
            lambda sid, **kw: _LostLockProxy(real_lock(sid, **kw)),
        )
        batch = await apply_gis_mutation_batch(
            clean_session,
            [PatchLayerPresentationIntent(layer_id="lost-1", visible=False)],
            origin="agent",
            actor="test",
        )
    assert batch.is_error is True
    assert "lost" in batch.error_msg.lower()
    state1 = await session_data_manager.get_map_state(clean_session)
    assert state1["_cartographic_mutation_revision"] == rev0, "lost 批不得递增 revision"
    by_id = {l["id"]: l for l in state1["mapspec"]["layers"]}
    assert by_id["lost-1"]["cartographic_intent"]["expected_visible"] is True


@pytest.mark.asyncio
async def test_concurrent_user_hide_vs_agent_finalize_batch(clean_session):
    """并发 user hide vs agent finalize：无论交错，用户决策最终存活。"""
    import asyncio
    await _seed_layers(clean_session, ["race-a", "race-b"])
    engine = mapspec_lifecycle_engine

    async def user_hide():
        # 用户 durable 隐藏 race-a（CAS 重试至成功 —— 与 agent 批竞态）
        for _ in range(5):
            state = await session_data_manager.get_map_state(clean_session)
            rev = state.get("_cartographic_mutation_revision", 0)
            res = await apply_gis_mutation(
                clean_session,
                PatchLayerPresentationIntent(layer_id="race-a", visible=False),
                origin="user",
                actor="test",
                expected_revision=rev,
            )
            if res.is_error is False and not res.superseded:
                return
            await asyncio.sleep(0)

    async def agent_finalize():
        for _ in range(5):
            state = await session_data_manager.get_map_state(clean_session)
            rev = state.get("_cartographic_mutation_revision", 0)
            batch = await apply_gis_mutation_batch(
                clean_session,
                [
                    PatchLayerPresentationIntent(layer_id="race-a", visible=True),
                    PatchLayerPresentationIntent(layer_id="race-b", visible=False),
                ],
                origin="agent",
                actor="finalize",
                expected_revision=rev,
            )
            if batch.committed or batch.refused_count > 0:
                return
            await asyncio.sleep(0)

    await asyncio.gather(user_hide(), agent_finalize())
    state = await session_data_manager.get_map_state(clean_session)
    by_id = {l["id"]: l for l in state["mapspec"]["layers"]}
    a_intent = by_id["race-a"]["cartographic_intent"]
    # 不变量：用户隐藏要么成立（owner=user/hidden），要么 agent 批从未翻回
    #（refused）—— 二者必居其一，绝无 agent-owned visible=true 覆盖用户隐藏。
    assert not (
        a_intent.get("presentation_owner") == "agent"
        and a_intent.get("expected_visible") is True
    ), f"用户隐藏被 agent 批覆盖: {a_intent}"
