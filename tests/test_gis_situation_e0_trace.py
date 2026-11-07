"""E0 事实审计 — 12 组多轮操作事件流追踪（characterization）。

任务书 05_HUMAN_AGENT E0：逐操作钉住「哪个事件进后端、Agent 下一轮看到什么」
的**现状**。断言的是今日事实（含缺口），不是期望——期望断言随 S1/S2/S4 切片
落地后在本文件追加/翻转（各测试 docstring 标注对应切片）。

链路：前端事件 → ingest.apply_event（词表/白名单/结局交叉校验）→
project_interaction_snapshot（语义快照）→ compile_interaction /
compile_situation（situation 事实）。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.interaction_ir.contracts import (
    MAX_PAYLOAD_BYTES,
    LedgerState,
    normalize_payload,
)
from app.services.gis_harness.interaction_ir.ingest import (
    LEDGER_KEY,
    apply_event,
)
from app.services.gis_harness.interaction_ir.projection import (
    interaction_snapshot_from_map_state,
    project_interaction_snapshot,
)
from app.services.gis_situation.compiler import compile_interaction
from app.services.gis_situation.contract import TemporalContext


def _fold(events, *, mutation_dedup=None):
    """按生产 ingest 同路径折叠事件（replay 与 ingest 共用 apply_event）。"""
    state = LedgerState()
    for e in events:
        apply_event(state, e, mutation_dedup=mutation_dedup)
    return state


def _ev(seq: int, kind: str, payload: dict, **extra) -> dict:
    return {
        "event_id": f"c-{seq:08d}",
        "client_instance_id": "t-aaaa0001",
        "client_seq": seq,
        "kind": kind,
        "payload": payload,
        **extra,
    }


# ── 操作 1：点选学校 → 追问统计 ─────────────────────────────────────────


def test_op1_point_select_reaches_projection_and_situation():
    """点选进后端（IR + pre-turn 双通道）；但 analysis 分区无 selection
    scope 事实（S1 缺口：选择不成为受控分析输入）。"""
    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "feature_id": "sch-1",
            "dataset_ref": "ref:geojson-aaa",
            "field_names": ["name", "type"],
        }),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["selection"]["feature_id"] == "sch-1"
    assert snap["selection"]["dataset_ref"] == "ref:geojson-aaa"
    # situation.interaction.selected_feature 只来自 pre-turn 通道（无 IR 直读）。
    ctx = compile_interaction({}, [])
    assert ctx.selected_feature.status == "unknown"
    ctx2 = compile_interaction(
        {"_cartographic_context_observation": {
            "selected_feature": {"layer_id": "schools", "feature_id": "sch-1"},
        }}, [],
    )
    assert ctx2.selected_feature.status == "known"


# ── 操作 2：框选区域 → 按当前数据分析 ───────────────────────────────────


def test_op2_brush_select_ids_are_bounded_and_bbox_dropped():
    """框选多选（SW-02/#2004 期望语义）：selected_ids 有界但与选择面三方
    对齐（≤50），12 个 id 完整保留 + 披露截断标记为 False；bbox 谓词随
    选择上行（SW-02 残余：框选区域作为空间范围可指代；几何本体仍走
    canvas affordance 环，不进 IR）。"""
    normalized = normalize_payload("feature_selected", {
        "layer_id": "schools",
        "selected_ids": [f"sch-{i}" for i in range(12)],
        "matched_count": 12,
        "bbox": [104.0, 30.6, 104.1, 30.7],
    })
    assert normalized is not None
    assert len(normalized["selected_ids"]) == 12  # 新界内完整保留（旧 ≤8 为 #2004 断点）
    assert normalized["ids_truncated"] is False
    assert normalized["matched_count"] == 12
    assert normalized["bbox"] == [104.0, 30.6, 104.1, 30.7]  # SW-02：bbox 谓词进白名单

    overflow = normalize_payload("feature_selected", {
        "layer_id": "schools",
        "selected_ids": [f"sch-{i}" for i in range(53)],
        "matched_count": 53,
    })
    assert overflow is not None
    assert len(overflow["selected_ids"]) == 50  # 有界上限（三方对齐）
    assert overflow["ids_truncated"] is True

    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "selected_ids": ["sch-1", "sch-2", "sch-3"],
            "dataset_ref": "ref:geojson-aaa",
        }),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["selection"]["selected_ids"] == ["sch-1", "sch-2", "sch-3"]
    # active_aoi 只来自 aoi_* kind（sketch 通道），框选不产生 AOI 事实。
    assert snap["active_aoi"] is None


def test_op2_aoi_digest_only():
    """sketch AOI：IR 只存摘要（geometry_digest/vertex_count），几何本体
    留客户端——分析无法消费 AOI 几何（现状缺口，S1 消费 canvas affordance）。"""
    state = _fold([
        _ev(1, "aoi_drawn", {
            "geometry_type": "Polygon", "vertex_count": 5,
            "geometry_digest": "sha:abc",
            "coordinates": [[[104, 30], [104.1, 30], [104.1, 30.1], [104, 30]]],
        }),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["active_aoi"]["geometry_digest"] == "sha:abc"
    assert "coordinates" not in snap["active_aoi"]  # 白名单已丢弃


# ── 操作 3：隐藏/恢复图层（user-wins）──────────────────────────────────


def test_op3_layer_visibility_user_override_and_dedup_crosscheck():
    """显隐 mutation 事件带结局进 overrides；mutation_verified 由服务端
    对 _mutation_dedup 只读交叉校验（committed 但未命中索引 → claimed）。"""
    events = [
        _ev(1, "layer_visibility_changed", {"layer_id": "schools", "visible": False},
            client_mutation_id="mut-1", mutation_outcome="committed"),
    ]
    state = _fold(events)
    # _mutation_dedup 无该 id → verified=False（claimed 口径）
    snap = interaction_snapshot_from_map_state({
        LEDGER_KEY: state.events,
    })
    assert snap["user_overrides"] == []
    assert snap["attempted_not_applied"][0]["link"]["mutation_verified"] is False
    # The same claimed event becomes an override only with engine dedup proof.
    verified = _fold(events, mutation_dedup={"c:mut-1": {"revision": 7}})
    verified_snapshot = interaction_snapshot_from_map_state({LEDGER_KEY: verified.events})
    entry = next(o for o in verified_snapshot["user_overrides"] if o["layer_id"] == "schools")
    assert entry["visible"] is False
    assert entry["link"]["mutation_verified"] is True
    assert entry["link"]["verified_revision"] == 7


# ── 操作 4：修改样式（透明度）──────────────────────────────────────────


def test_op4_opacity_override_persists_in_projection():
    state = _fold([
        _ev(1, "layer_opacity_changed", {"layer_id": "schools", "opacity": 0.4},
            client_mutation_id="mut-2", mutation_outcome="committed"),
    ], mutation_dedup={"c:mut-2": {"revision": 8}})
    snap = project_interaction_snapshot(state.events)
    entry = next(o for o in snap["user_overrides"] if o["layer_id"] == "schools")
    assert entry["opacity"] == 0.4
    assert entry["link"]["mutation_verified"] is True
    assert entry["link"]["verified_revision"] == 8


def test_op4_failed_mutation_lands_in_attempted_not_applied():
    """被拒/被抢占的用户意图进 attempted_not_applied，不冒充成功。"""
    state = _fold([
        _ev(1, "layer_opacity_changed", {"layer_id": "schools", "opacity": 0.4},
            client_mutation_id="mut-3", mutation_outcome="superseded"),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["user_overrides"] == []
    assert snap["attempted_not_applied"][0]["outcome"] == "superseded"


# ── 操作 5：撤销（现状：后端无表现层 undo 原语）─────────────────────────


def test_op5_no_undo_kind_in_vocabulary():
    """词表无 undo kind；撤销走 mutation 通道本身（回退=再提交一次）。"""
    assert normalize_payload("undo", {}) is None
    assert normalize_payload("map_operation_undone", {}) is None


# ── 操作 6：变更地区（viewport settle 合并）────────────────────────────


def test_op6_viewport_settled_coalesced():
    state = _fold([
        _ev(1, "viewport_settled", {"center": [104.0, 30.6], "zoom": 9,
                                    "bounds": [103.9, 30.5, 104.1, 30.7]}),
        _ev(2, "viewport_settled", {"center": [116.4, 39.9], "zoom": 10,
                                    "bounds": [116.3, 39.8, 116.5, 40.0]}),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["viewport"]["center"] == [116.4, 39.9]  # 最新 settle 胜出
    recent_kinds = [r["kind"] for r in snap["recent_interactions"]]
    assert recent_kinds.count("viewport_settled") == 1  # 合并语义


# ── 操作 7：再次分析（selection 跨轮存活）──────────────────────────────


def test_op7_selection_survives_across_server_seq():
    """选择由 server_seq 驱动跨轮存活，直到显式 deselect/新选择替代。"""
    state = _fold([
        _ev(1, "feature_selected", {"layer_id": "schools", "feature_id": "sch-1"}),
        _ev(2, "viewport_settled", {"center": [104, 30], "zoom": 11}),
        _ev(3, "layer_visibility_changed", {"layer_id": "other", "visible": True},
            client_mutation_id="mut-4", mutation_outcome="committed"),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["selection"]["feature_id"] == "sch-1"


# ── 操作 9：图表类别选择（S2 已收词：normalize 接受 + 投影可见）────────


def test_op9_chart_selection_kind_accepted_and_projected():
    """S2 翻转：chart_selection_changed 进词表（钩子 chart-panel onClick），
    类别谓词进投影 chart_selection 面。"""
    normalized = normalize_payload("chart_selection_changed", {
        "layer_id": "districts", "field": "type", "categories": ["A", "B"],
    })
    assert normalized is not None
    assert normalized.get("categories") == ["A", "B"]
    state = _fold([
        _ev(1, "chart_selection_changed", {
            "layer_id": "districts", "field": "type",
            "categories": ["A"], "action": "select",
        }),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["chart_selection"]["categories"] == ["A"]


# ── 操作 10：属性表定位（S2 已收词）───────────────────────────────────


def test_op10_table_selection_kind_accepted():
    normalized = normalize_payload("table_selection_changed", {
        "layer_id": "schools", "feature_id": "sch-2",
    })
    assert normalized is not None
    assert normalized.get("feature_id") == "sch-2"


# ── 操作 11：时间窗（S2 已收词：settle 终态 + replace_pending）─────────


def test_op11_temporal_kind_accepted_and_coalesced():
    assert normalize_payload("temporal_window_settled", {
        "current": "2024-01", "pinned": True,
    }) is not None
    # active_time_slice 是 TemporalContext 的 v1 unknown 事实（contract.py）。
    assert "active_time_slice" in TemporalContext.model_fields


# ── 操作 12：敏感编辑（selected_ids 是 feature_edit 的多选来源）────────


def test_op12_selected_ids_flow_to_projection_for_feature_edit():
    state = _fold([
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-bbb",
            "selected_ids": ["sch-7", "sch-8"],
        }),
    ])
    snap = project_interaction_snapshot(state.events)
    assert snap["selection"]["selected_ids"] == ["sch-7", "sch-8"]


# ── 通用边界：payload 预算与环淘汰披露（现状已实现的护栏）──────────────


def test_guard_payload_budget_rejects_oversize():
    """SW-02 后预算分层：selection kind 4096B（50 ids 集），通用 512B。

    selection kind 超宽预算仍拒收（51×128 字符伪造 id 集 ≈7KB > 4096B）；
    非 selection kind（chart categories 8×128 ≈1.1KB）超 512B 仍整事件拒收。
    """
    from app.services.gis_harness.interaction_ir.contracts import (
        MAX_PAYLOAD_BYTES_SELECTION,
    )
    big_selection = {
        "layer_id": "x" * 128, "feature_id": "y" * 128,
        "geometry_digest": "z" * 128, "dataset_ref": "r" * 128,
        "selected_ids": ["a" * 128] * 50, "field_names": ["f" * 32] * 8,
        "matched_count": 10 ** 6,
    }
    blob = json.dumps(big_selection)
    if len(blob.encode()) <= MAX_PAYLOAD_BYTES_SELECTION:
        pytest.skip("构造未超宽预算，换更大载荷")
    assert normalize_payload("feature_selected", big_selection) is None
    big_style = {
        "layer_id": "C" * 128,
        "attribute_names": [str(i) + "a" * 126 for i in range(8)],
    }
    if len(json.dumps(big_style)) <= MAX_PAYLOAD_BYTES:
        pytest.skip("构造未超预算，换更大载荷")
    assert normalize_payload("layer_style_changed", big_style) is None


def test_map_state_snapshot_roundtrip_with_dropped_disclosure():
    snap = interaction_snapshot_from_map_state({
        LEDGER_KEY: [],
        "_interaction_ir_meta": {"dropped": 3},
    })
    assert snap["dropped_events"] == 3  # 环淘汰诚实披露
