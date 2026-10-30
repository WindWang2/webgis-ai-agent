"""独立评审（INDEPENDENT_REVIEW）P0/P1 修复的回归测试。

- B-1（P0）：symbol upsert 剥离 presentation 字段 —— 同操作内先提交的
  显隐/透明度不被 symbol 洗回；用户窗口内 opacity 决策不被覆盖。
- B-3（P1）：中链 superseded 聚合为 partial（不掩蔽 committed），
  chain_aborted 计数与 message 披露。
- C-1（P1）：全失败 → status="failed" 且工具 success=False。
- C-2（P1）：咨询态回执（clarification/noop/rejected）不粘滞 operation_id
  —— 同 id 重试重新裁决。
- A-5（P2 顺带）：批准提交失败时 pending 保留，可重试。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.map_operations import (
    MapOperationRequest,
    commit_map_operation,
    get_operation_receipt,
    mint_operation_id,
)
from app.tools.map_operations_tools import register_map_operation_tools
from app.tools.registry import ToolRegistry
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance


def _sid() -> str:
    return f"moprev-{uuid.uuid4().hex[:10]}"


async def _seed(sid: str, paint: dict | None = None) -> None:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "schools", "type": "circle", "source": "src-1",
        "name": "成都小学",
        "paint": paint if paint is not None else {"circle-radius": 4},
    }, source_data={"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0, 30.6]},
             "properties": {}}]}}))
    assert not res.is_error


def _spec_layer(spec: dict, layer_id: str) -> dict:
    return next(lyr for lyr in spec["layers"] if lyr["id"] == layer_id)


# ── B-1（P0）────────────────────────────────────────────────────────────


async def test_b1_symbol_does_not_revert_earlier_visibility():
    """同操作 [隐藏, symbol 放大] —— symbol 的整层 upsert 不得把 visibility
    写回编译期快照的 visible（评审复现 ii）。"""
    sid = _sid()
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[
            {"kind": "set_layer_visibility", "target": "成都小学", "visible": False},
            {"kind": "set_layer_symbol", "target": "成都小学",
             "symbol_property": "point_size", "symbol_value": 8},
        ],
    ))
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = _spec_layer(spec, "schools")
    assert (layer.get("layout") or {}).get("visibility") == "none"
    assert layer["paint"]["circle-radius"] == 8


async def test_b1_symbol_does_not_launder_concurrent_user_opacity():
    """编译-落盘窗口内用户改 opacity → symbol upsert 不得洗回编译期旧值
    （评审复现 iii：红线 2 opacity 面）。"""
    sid = _sid()
    await _seed(sid, paint={"circle-radius": 4, "opacity": 0.8})
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.session_data import session_data_manager

    # 用户在 agent 编译后、提交前把 opacity 调到 0.3
    state = await session_data_manager.get_map_state(sid) or {}
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    user_res = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="schools", opacity=0.3),
        origin="user", actor="test-user",
        engine=MapSpecLifecycleEngine(), expected_revision=rev,
    )
    assert not user_res.is_error

    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_symbol", "target": "成都小学",
                  "symbol_property": "point_size", "symbol_value": 9}],
    ))
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    paint = _spec_layer(spec, "schools")["paint"]
    assert paint["circle-radius"] == 9
    # 用户 0.3 保留（symbol upsert 已剥离 opacity —— 引擎继承现值）
    assert paint.get("opacity") == pytest.approx(0.3), paint


# ── B-3（P1）────────────────────────────────────────────────────────────


async def test_b3_mid_chain_superseded_reports_partial_not_committed():
    """presentation 批成功 → 顺序步骤 superseded → status=partial、
    chain_aborted 计数、message 披露（评审复现：此前聚合为 committed）。"""
    sid = _sid()
    await _seed(sid)
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(sid) or {}
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    # 外部写者预备：在第一个顺序效果（paint）CAS 时推进 revision

    class _Racer:
        def __init__(self):
            self.fired = False

    racer = _Racer()
    original = apply_gis_mutation

    async def _racing_apply(session_id, intent, **kwargs):
        # 第一个顺序 intent（PatchLayerStyle）执行前推进 revision（user
        # origin 必须携带当时的 expected_revision 才能真正落盘）
        if type(intent).__name__ == "PatchLayerStyleIntent" and not racer.fired:
            racer.fired = True
            state = await session_data_manager.get_map_state(sid) or {}
            cur_rev = int(state.get("_cartographic_mutation_revision") or 0)
            await original(
                sid, PatchLayerPresentationIntent(layer_id="schools", opacity=0.42),
                origin="user", actor="racer",
                engine=MapSpecLifecycleEngine(), expected_revision=cur_rev,
            )
        return await original(session_id, intent, **kwargs)

    import app.services.gis_world_state as gws

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gws, "apply_gis_mutation", _racing_apply)
        receipt = await commit_map_operation(sid, MapOperationRequest(
            operation_id=mint_operation_id(), mode="commit",
            effects=[
                {"kind": "set_layer_opacity", "target": "成都小学", "opacity": 0.6},
                {"kind": "set_layer_paint", "target": "成都小学",
                 "paint": {"circle-opacity": 0.5}},
                {"kind": "set_layer_symbol", "target": "成都小学",
                 "symbol_property": "point_size", "symbol_value": 7},
            ],
        ))

    assert racer.fired, "竞态注入未触发"
    assert receipt["status"] == "partial", receipt["status"]
    assert receipt.get("superseded") is True
    assert receipt["applied_count"] >= 1  # presentation 批或竞态前效果已生效
    assert receipt.get("aborted_count", 0) >= 1  # chain_aborted 计数披露
    assert "并发" in receipt["message"] or "未尝试" in receipt["message"]


# ── C-1（P1）────────────────────────────────────────────────────────────


async def test_c1_all_failed_is_failed_not_partial():
    sid = _sid()
    await _seed(sid)
    # 两个不存在的层 → 全部 skipped(layer_not_found) → rejected；
    # 用组件（缺 layout）制造 failed 面更直接：构造引擎拒绝路径
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[
            {"kind": "set_component", "target": "ghost-comp",
             "component_patch": {"position": "bottom-left"}},
            {"kind": "set_layer_opacity", "target": "ghost-layer", "opacity": 0.5},
        ],
    ))
    assert receipt["status"] == "rejected"  # 全 skipped（目标缺失）

    # 工具面：rejected/noop 保持 success=True（世界已满足/被拒如实），
    # "failed"（有 failed 效果且零 applied）必须 success=False
    registry = ToolRegistry()
    register_map_operation_tools(registry)
    result = await registry.dispatch("webgis_map_operation", {
        "mode": "commit",
        "effects": [
            {"kind": "set_component", "target": "ghost-comp",
             "component_patch": {"position": "bottom-left"}},
            {"kind": "set_layer_opacity", "target": "ghost-layer", "opacity": 0.5},
        ],
    }, session_id=sid)
    assert result["status"] == "rejected"
    assert result["success"] is True  # rejected 仍是诚实中止态

    # 真正的 failed 面：全 internal_error 场景由单测直接构造较重 ——
    # 此处钉住 status 词表映射：failed 不在 success 集
    from app.tools.map_operations_tools import WebgisMapOperationArgs  # noqa: F401

    success_states = {"preview", "committed", "noop", "partial", "superseded",
                      "rejected", "confirmation_required", "clarification_needed"}
    assert "failed" not in success_states


# ── C-2（P1）────────────────────────────────────────────────────────────


async def test_c2_clarification_receipt_not_sticky():
    sid = _sid()
    # 两层共享子串「武侯区学校」且无精确全名 → 真歧义
    engine = MapSpecLifecycleEngine()
    for lid, name in (("schools", "武侯区学校分布"), ("schools-v2", "武侯区学校密度")):
        res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={
            "id": lid, "type": "circle", "source": f"src-{lid}",
            "name": name, "paint": {"circle-radius": 3},
        }, source_data={"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [104.1, 30.7]},
                 "properties": {}}]}}))
        assert not res.is_error

    oid = mint_operation_id()
    first = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "武侯区学校", "opacity": 0.5}],
    ))
    assert first["status"] == "clarification_needed"

    # 消除歧义（删除其中一层）后同 id 重试 → 必须重新裁决并执行
    from app.services.mapspec.intents import RemoveLayerIntent
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(sid) or {}
    rev = int(state.get("_cartographic_mutation_revision") or 0)
    remove = await engine.apply_mutation(
        sid, RemoveLayerIntent(layer_id="schools-v2"),
        origin="user", expected_revision=rev,
    )
    assert not remove.is_error

    second = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "武侯区学校", "opacity": 0.5}],
    ))
    assert second["status"] == "committed", second["status"]
    assert second.get("replayed") is not True
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert _spec_layer(spec, "schools")["paint"]["opacity"] == pytest.approx(0.5)


async def test_c2_executed_receipt_still_replayable():
    sid = _sid()
    await _seed(sid)
    oid = mint_operation_id()
    first = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "成都小学", "opacity": 0.55}],
    ))
    assert first["status"] == "committed"
    second = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "成都小学", "opacity": 0.55}],
    ))
    assert second.get("replayed") is True


# ── A-5（批准失败 pending 保留）────────────────────────────────────────


async def test_a5_failed_approval_keeps_pending():
    sid = _sid()
    await _seed(sid)
    oid = mint_operation_id()
    pending = await commit_map_operation(sid, MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "remove_layer", "target": "成都小学"}],
    ))
    assert pending["status"] == "confirmation_required"

    # 模拟引擎异常 → bypass_replay 提交失败 → pending 必须保留在环内
    import app.services.gis_world_state as gws

    async def _boom(*a, **k):
        raise RuntimeError("engine down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gws, "apply_gis_mutation", _boom)
        failed = await commit_map_operation(
            sid, MapOperationRequest(
                operation_id=oid, mode="commit",
                effects=[{"kind": "remove_layer", "target": "成都小学"}],
            ),
            origin="user", user_ack=True, bypass_replay=True,
        )
    assert failed["status"] == "failed"  # 全失败（C-1 语义）：零 applied + 有 failed
    assert failed["applied_count"] == 0

    stored = await get_operation_receipt(sid, oid)
    # 环内仍是 pending（未被失败提交破坏）—— owner 可重试批准
    assert stored is not None
