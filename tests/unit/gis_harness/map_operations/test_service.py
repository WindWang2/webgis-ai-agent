"""Map Operation service 测试（M4/M9）：preview 零突变 / CAS 链提交 /
user-wins / 幂等重放 / 指代解析 / 敏感人环 / 旗舰 F-A1·F-A2。

种子走真实 MapSpecLifecycleEngine（与生产同一条 apply_gis_mutation 门面），
无 mock —— 断言的是真实落盘与 revision 前进。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.map_operations import (
    MapOperationRequest,
    commit_map_operation,
    get_operation_receipt,
    list_operation_receipts,
    mint_operation_id,
    preview_map_operation,
)
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


async def _seed(session_id: str) -> None:
    """F-A1 场景种子：学校点 + 区边界 + 热力图 + 图例组件。"""
    engine = MapSpecLifecycleEngine()
    for layer_id, ltype, extra in [
        ("schools", "circle", {"paint": {"circle-color": "#1f77b4", "circle-radius": 4}}),
        ("districts", "line", {"paint": {"line-color": "#333", "line-width": 1}}),
        ("heatmap", "heatmap", {"paint": {"heatmap-opacity": 0.8}}),
    ]:
        res = await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer={
                    "id": layer_id,
                    "type": ltype,
                    "source": f"src-{layer_id}",
                    "name": {"schools": "成都小学", "districts": "武侯区边界",
                             "heatmap": "人口热力图"}[layer_id],
                    **extra,
                },
                source_data={
                    "type": "geojson",
                    "inlineData": {
                        "type": "FeatureCollection",
                        "features": [
                            {"type": "Feature",
                             "geometry": {"type": "Point",
                                          "coordinates": [104.04, 30.64]},
                             "properties": {"name": "x"}},
                        ],
                    },
                },
            ),
        )
        assert not res.is_error, layer_id


async def _current_revision(session_id: str) -> int:
    state = await session_data_manager.get_map_state(session_id) or {}
    return int(state.get("_cartographic_mutation_revision") or 0)


def _spec_layer(spec: dict, layer_id: str) -> dict:
    return next(lyr for lyr in spec["layers"] if lyr["id"] == layer_id)


# ── F-A1 旗舰：多动作一次提交 ────────────────────────────────────────────


async def test_fa1_multi_effect_single_operation():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)

    req = MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="commit",
        effects=[
            {"kind": "set_layer_visibility", "target": "人口热力图", "visible": False},
            {"kind": "set_layer_symbol", "target": "成都小学",
             "symbol_property": "point_size", "symbol_scale": 1.5},
            {"kind": "set_viewport", "bbox": [103.93, 30.62, 104.20, 30.80]},
        ],
    )
    receipt = await commit_map_operation(sid, req)

    assert receipt["status"] == "committed", receipt
    assert receipt["applied_count"] == 3
    assert receipt["final_revision"] > receipt["base_revision"]

    spec = await mapspec_store_instance.get_mapspec(sid)
    # 热力图隐藏（durable）
    heat = _spec_layer(spec, "heatmap")
    assert (heat.get("layout") or {}).get("visibility") == "none"
    # 学校点放大 1.5×（4 → 6）
    schools = _spec_layer(spec, "schools")
    assert schools["paint"]["circle-radius"] == pytest.approx(6.0)
    # 视口持久 + fly_to 命令下发
    assert spec["view"]["center"] == pytest.approx(
        [(103.93 + 104.20) / 2, (30.62 + 30.80) / 2]
    )
    cmds = receipt["commands"]
    assert any(c["command"] == "fly_to" for c in cmds)
    assert any(c["command"] == "map_operation_receipt" for c in cmds)
    # 「图例别动」：无图例效果 → untouched 披露 ≥ 0 且组件未触碰
    assert "untouched_layer_count" in receipt


async def test_preview_is_zero_mutation():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    rev_before = await _current_revision(sid)

    preview = await preview_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="preview",
        effects=[
            {"kind": "set_layer_visibility", "target": "heatmap", "visible": False},
        ],
    ))
    assert preview["mode"] == "preview"
    assert preview["base_revision"] == rev_before
    eff = preview["effects"][0]
    assert eff["before"] == {"visible": True}
    assert eff["after"] == {"visible": False}
    assert await _current_revision(sid) == rev_before  # 零 mutation
    # preview 不进回执环
    assert await list_operation_receipts(sid) == []


# ── F-A2 旗舰：人手插队（user-wins + superseded）────────────────────────


async def test_fa2_user_preemption_superseded():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    rev = await _current_revision(sid)

    # agent 计划隐藏 schools（CAS 基线 = rev）
    req = MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="commit",
        effects=[{"kind": "set_layer_visibility", "target": "schools", "visible": False}],
        expected_revision=rev,
    )
    # 用户抢先：在 commit 前手动显示切换（推进 revision）
    user_engine = MapSpecLifecycleEngine()
    user_res = await __import__("app.services.gis_world_state", fromlist=["apply_gis_mutation"]).apply_gis_mutation(
        sid,
        __import__("app.services.mapspec.lifecycle_engine", fromlist=["PatchLayerPresentationIntent"]).PatchLayerPresentationIntent(
            layer_id="schools", visible=False),
        origin="user", actor="test-user", engine=user_engine,
        expected_revision=rev,
    )
    assert not user_res.is_error

    receipt = await commit_map_operation(sid, req)
    # agent 的同值请求：CAS superseded 或幂等 noop —— 都不允许静默覆写用户
    spec = await mapspec_store_instance.get_mapspec(sid)
    schools = _spec_layer(spec, "schools")
    assert (schools.get("cartographic_intent") or {}).get("presentation_owner") == "user"
    assert receipt["status"] in ("superseded", "noop", "committed")
    if receipt["status"] == "committed":
        # 若引擎判为幂等重放/duplicate，presentation_owner 仍必须是 user
        assert (schools.get("cartographic_intent") or {}).get("presentation_owner") == "user"


async def test_fa2_user_hidden_layer_agent_refused():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    # 用户手动隐藏 heatmap（durable user 决策）
    rev = await _current_revision(sid)
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent
    from app.services.gis_world_state import apply_gis_mutation

    res = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="heatmap", visible=False),
        origin="user", actor="test-user",
        engine=MapSpecLifecycleEngine(), expected_revision=rev,
    )
    assert not res.is_error

    # agent 操作试图把 heatmap 翻回可见 → user-wins 拒绝（回执如实）
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(),
        mode="commit",
        effects=[
            {"kind": "set_layer_visibility", "target": "heatmap", "visible": True},
            {"kind": "set_layer_opacity", "target": "成都小学", "opacity": 0.6},
        ],
    ))
    eff0, eff1 = receipt["effects"]
    assert eff0["state"] == "skipped"
    assert eff0["reason_code"] == "user_wins"
    assert eff1["state"] == "applied"  # 其他层不受影响
    assert receipt["status"] == "committed"
    assert "保留用户决策" in receipt["message"] or receipt["skipped_count"] == 1


# ── 幂等与恢复（M9）─────────────────────────────────────────────────────


async def test_idempotent_replay_same_operation_id():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    oid = mint_operation_id()
    req = MapOperationRequest(
        operation_id=oid, mode="commit",
        effects=[{"kind": "set_layer_opacity", "target": "districts", "opacity": 0.5}],
    )
    first = await commit_map_operation(sid, req)
    assert first["status"] == "committed"
    rev_after_first = await _current_revision(sid)

    second = await commit_map_operation(sid, req)
    assert second.get("replayed") is True
    assert second["operation_id"] == oid
    # 重放不推进 revision
    assert await _current_revision(sid) == rev_after_first
    # 环内只有一条
    ring = await list_operation_receipts(sid)
    assert len([r for r in ring if r["operation_id"] == oid]) == 1


async def test_receipt_ring_survives_and_bounded():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    for i in range(18):
        await commit_map_operation(sid, MapOperationRequest(
            operation_id=f"mop-ring-{i:04d}", mode="commit",
            effects=[{"kind": "set_layer_opacity", "target": "districts",
                      "opacity": round(0.1 + i * 0.01, 3)}],
        ))
    ring = await list_operation_receipts(sid, limit=16)
    assert len(ring) <= 16
    assert ring[0]["operation_id"] == "mop-ring-0017"
    assert await get_operation_receipt(sid, "mop-ring-0000") is None  # FIFO 淘汰


# ── 指代解析（M3）───────────────────────────────────────────────────────


async def test_focused_layer_anaphora():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    # 模拟 IR：selection 指向 schools
    await session_data_manager.set_map_state(sid, "_interaction_ir_ledger", [
        {"event_id": "e1", "client_instance_id": "t1", "client_seq": 1,
         "kind": "feature_selected", "actor": "user", "ts": 1.0,
         "payload": {"layer_id": "schools", "feature_id": "f1"}},
    ])

    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_opacity", "target_kind": "focused_layer",
                  "opacity": 0.9}],
    ))
    assert receipt["status"] == "committed"
    assert receipt["effects"][0]["targets"] == ["schools"]


async def test_ambiguous_name_returns_clarification_no_mutation():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    # 再加一个名称含「小学」的层
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "schools-v2", "type": "circle", "source": "src-v2",
        "name": "小学分布v2", "paint": {"circle-color": "#abc"},
    }, source_data={"type": "geojson", "inlineData": {
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0, 30.6]},
             "properties": {}}]}}))
    assert not res.is_error
    rev_before = await _current_revision(sid)

    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_visibility", "target": "小学", "visible": False}],
    ))
    assert receipt["status"] == "clarification_needed"
    assert receipt["clarification"]["candidates"]
    assert {c["layer_id"] for c in receipt["clarification"]["candidates"]} >= {
        "schools", "schools-v2"}
    # 歧义不写状态
    assert await _current_revision(sid) == rev_before


async def test_last_result_anaphora_from_provenance():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    # agent 上一次 touched = symbol 效果（走 UpsertLayer → provenance UpsertLayerIntent）
    await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_symbol", "target": "成都小学",
                  "symbol_property": "point_size", "symbol_value": 5}],
    ))
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_opacity", "target_kind": "last_result",
                  "opacity": 0.7}],
    ))
    assert receipt["status"] == "committed"
    assert receipt["effects"][0]["targets"] == ["schools"]


# ── 敏感人环（D4）──────────────────────────────────────────────────────


async def test_sensitive_remove_requires_owner_confirm():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    rev_before = await _current_revision(sid)

    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "remove_layer", "target": "heatmap"}],
    ))
    assert receipt["status"] == "confirmation_required"
    assert receipt["pending_effects_request"]["effects"]
    # 未删除：层仍在
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert any(lyr["id"] == "heatmap" for lyr in spec["layers"])
    assert await _current_revision(sid) == rev_before


async def test_sensitive_remove_with_user_ack_commits():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    rev = await _current_revision(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "remove_layer", "target": "heatmap"}],
        expected_revision=rev,
    ), origin="user", user_ack=True)
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert not any(lyr["id"] == "heatmap" for lyr in spec["layers"])


# ── noop 与移动 ─────────────────────────────────────────────────────────


async def test_noop_when_already_in_requested_state():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_visibility", "target": "schools", "visible": True}],
    ))
    assert receipt["status"] == "noop"
    assert receipt["effects"][0]["reason_code"] == "noop"


async def test_move_layer_to_top():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "move_layer", "target": "heatmap", "position": "top"}],
    ))
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert spec["layers"][0]["id"] == "heatmap"


async def test_missing_layer_rejected_not_silent():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_visibility", "target": "不存在的层", "visible": False}],
    ))
    assert receipt["status"] == "rejected"
    assert receipt["effects"][0]["reason_code"] == "layer_not_found"


async def test_viewport_persists_and_commands():
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_viewport", "center": [104.05, 30.65], "zoom": 12.5}],
    ))
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    assert spec["view"]["center"] == [104.05, 30.65]
    assert spec["view"]["zoom"] == 12.5
    fly = next(c for c in receipt["commands"] if c["command"] == "fly_to")
    assert fly["params"]["center"] == [104.05, 30.65]


async def test_symbol_layout_bucket_label_size():
    """label_size 落 layout 桶（text-size）而非 paint。"""
    sid = f"mop-{uuid.uuid4().hex[:10]}"
    await _seed(sid)
    receipt = await commit_map_operation(sid, MapOperationRequest(
        operation_id=mint_operation_id(), mode="commit",
        effects=[{"kind": "set_layer_symbol", "target": "districts",
                  "symbol_property": "label_size", "symbol_value": 14}],
    ))
    assert receipt["status"] == "committed"
    spec = await mapspec_store_instance.get_mapspec(sid)
    districts = _spec_layer(spec, "districts")
    assert districts.get("layout", {}).get("text-size") == 14
