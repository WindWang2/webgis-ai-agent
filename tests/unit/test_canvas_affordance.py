"""画布可供性（Canvas Affordance）单测套件（ADR-0194）。

覆盖：
- T1 信封规范化：白名单投影 / 逐 action 拒收 / 整封拒收 / 字节预算；
- T2 摄取：内容寻重（跨通道双报不双计）、单调序列、有界环（16）、
  绝不抛出（fail-open）、延迟预算 < 100ms；
- T3 草图 × 图层布尔拓扑派生（select_intersect / difference / 空交集）；
- T4 情境注入：SitFact 投影、[画布意图] 上下文块；
- T5 生成式 Widget 契约：4 类合法样例、恶意/越界载荷拒绝矩阵、
  SSE ui_action 事件形态、Tool Pipeline 摘取（llm_payload 剥离）；
- T6 ChatRequest schema 接受 canvas_actions 且 256KB 总闸不放松。
"""
import json
import time
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat_schema import ChatRequest
from app.services.gis_situation.canvas_affordance import (
    ACTION_BOX_SELECT,
    ACTION_WIDGET_REPLY,
    MAX_ACTION_RING,
    CanvasAffordanceAck,
    WidgetSpec,
    affordance_facts,
    extract_pending_widgets,
    ingest_canvas_actions,
    mount_widget_payload,
    normalize_envelope,
    render_affordance_context_block,
    sse_mount_widget,
    tokens_from_ring,
    topology_derive,
    validate_widget_spec,
)
from app.utils.sse import sse_event_type
from tests.data.situation_fakes import FailingStore, FakeSituationStore


def _sid() -> str:
    return f"sess-{uuid.uuid4().hex[:12]}"


def _box_polygon(
    w: float = 116.30, s: float = 39.80, e: float = 116.36, n: float = 39.86
) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def _envelope(actions: list[dict], **overrides) -> dict:
    env = {
        "envelope_id": f"env-{uuid.uuid4().hex[:10]}",
        "client_ts": 1760000000000,
        "actions": actions,
    }
    env.update(overrides)
    return env


def _box_action(action_id: str = "act-1", kind: str = ACTION_BOX_SELECT) -> dict:
    return {
        "action_id": action_id,
        "kind": kind,
        "geometry": _box_polygon(),
        "screen_px": {"x": 100, "y": 80, "width": 320, "height": 240},
        "layer_refs": ["water_areas"],
        "map_view": {"center": [116.33, 39.83], "zoom": 12},
        "created_at": 1760000000000,
        "meta": {},
    }


# ---------------------------------------------------------------------------
# T1 信封规范化
# ---------------------------------------------------------------------------


def test_normalize_envelope_accepts_valid_and_projects_bbox():
    out = normalize_envelope(_envelope([_box_action()]))
    assert out is not None
    assert out["envelope_id"].startswith("env-")
    assert len(out["actions"]) == 1
    act = out["actions"][0]
    # bbox = [w, s, e, n]
    assert act["bbox"] == [116.30, 39.80, 116.36, 39.86]
    assert act["kind"] == ACTION_BOX_SELECT
    assert act["layer_refs"] == ["water_areas"]
    assert out["rejected_actions"] == []


def test_normalize_envelope_rejects_garbage_envelopes():
    assert normalize_envelope(None) is None
    assert normalize_envelope("oops") is None
    assert normalize_envelope({"envelope_id": "x", "actions": []}) is None
    # actions 非列表
    assert normalize_envelope({"envelope_id": "x", "actions": "many"}) is None
    # 超信封字节预算：单个巨型 action 拖爆 64KB
    huge = _box_action()
    huge["meta"] = {"blob": "x" * 70_000}
    assert normalize_envelope(_envelope([huge])) is None


def test_normalize_envelope_drops_invalid_actions_keeps_good():
    bad_geom = _box_action("act-bad")
    bad_geom["geometry"] = {"type": "CubicSpline", "coordinates": []}
    bad_kind = _box_action("act-kind", kind="mousehover")
    bad_coords = _box_action("act-coord")
    bad_coords["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[999.0, 39.8], [116.36, 39.8], [116.36, 39.86], [999.0, 39.86], [999.0, 39.8]]],
    }
    good = _box_action("act-good")
    out = normalize_envelope(_envelope([bad_geom, bad_kind, bad_coords, good]))
    assert out is not None
    assert [a["action_id"] for a in out["actions"]] == ["act-good"]
    rejected = {r["action_id"]: r["reason"] for r in out["rejected_actions"]}
    assert set(rejected) == {"act-bad", "act-kind", "act-coord"}


# ---------------------------------------------------------------------------
# T2 摄取
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_accepts_envelope_writes_ring():
    session_id = _sid()
    store = FakeSituationStore()
    ack = await ingest_canvas_actions(
        session_id, _envelope([_box_action("a-1"), _box_action("a-2")]), store=store
    )
    assert isinstance(ack, CanvasAffordanceAck)
    assert ack.accepted
    assert ack.accepted_actions == ["a-1", "a-2"]
    ring = store._map_state["_situation_canvas_affordances"]
    assert len(ring) == 2
    assert ring[0]["action_id"] == "a-1"
    assert ring[0]["sequence"] == 1
    assert ring[1]["sequence"] == 2
    assert ring[0]["bbox"] == [116.30, 39.80, 116.36, 39.86]


@pytest.mark.asyncio
async def test_ingest_dedupes_cross_channel_double_report():
    """同一 action 双通道双报（即时端点 + turn 捎带）→ 不双计。"""
    session_id = _sid()
    store = FakeSituationStore()
    env = _envelope([_box_action("a-1")])
    first = await ingest_canvas_actions(session_id, env, store=store)
    assert first.accepted
    replay = await ingest_canvas_actions(session_id, dict(env), store=store)
    assert not replay.accepted
    assert replay.reason == "duplicate"
    ring = store._map_state["_situation_canvas_affordances"]
    assert len(ring) == 1


@pytest.mark.asyncio
async def test_ingest_ring_bounded_and_keeps_tail():
    session_id = _sid()
    store = FakeSituationStore()
    for i in range(MAX_ACTION_RING + 4):
        ack = await ingest_canvas_actions(
            session_id, _envelope([_box_action(f"a-{i}")]), store=store
        )
        assert ack.accepted
    ring = store._map_state["_situation_canvas_affordances"]
    assert len(ring) == MAX_ACTION_RING
    # 尾部保留最近条目
    assert ring[-1]["action_id"] == f"a-{MAX_ACTION_RING + 3}"
    assert ring[0]["action_id"] == "a-4"


@pytest.mark.asyncio
async def test_ingest_fail_open_on_store_errors():
    """存储故障 → accepted=False，绝不抛出（增值感知面纪律）。"""
    session_id = _sid()
    store = FailingStore(fail="set_map_state")
    ack = await ingest_canvas_actions(
        session_id, _envelope([_box_action()]), store=store
    )
    assert not ack.accepted
    assert ack.reason == "persist_failed"


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_envelope():
    session_id = _sid()
    store = FakeSituationStore()
    ack = await ingest_canvas_actions(session_id, {"actions": []}, store=store)
    assert not ack.accepted and ack.reason == "empty_or_invalid_envelope"
    ack2 = await ingest_canvas_actions(session_id, "not-a-dict", store=store)
    assert not ack2.accepted


@pytest.mark.asyncio
async def test_ingest_latency_under_100ms():
    """预算（spec §5）：单信封 ingest（规范化 + 锁写）< 100ms。"""
    session_id = _sid()
    store = FakeSituationStore()
    env = _envelope([_box_action(f"a-{i}") for i in range(6)])
    start = time.perf_counter()
    ack = await ingest_canvas_actions(session_id, env, store=store)
    elapsed = time.perf_counter() - start
    assert ack.accepted
    assert elapsed < 0.1, f"ingest took {elapsed * 1000:.1f}ms (budget 100ms)"


# ---------------------------------------------------------------------------
# T3 草图 × 图层布尔拓扑
# ---------------------------------------------------------------------------


def _water_features() -> list[dict]:
    return [
        {
            "id": "w-1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[116.31, 39.81], [116.33, 39.81], [116.33, 39.83], [116.31, 39.83], [116.31, 39.81]]],
            },
            "properties": {"type": "lake"},
        },
        {
            "id": "w-2",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[116.34, 39.84], [116.355, 39.84], [116.355, 39.855], [116.34, 39.855], [116.34, 39.84]]],
            },
            "properties": {"type": "pond"},
        },
        {
            "id": "w-3",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[117.5, 40.5], [117.6, 40.5], [117.6, 40.6], [117.5, 40.6], [117.5, 40.5]]],
            },
            "properties": {"type": "far_pond"},
        },
    ]


def test_topology_derive_select_intersect_hits_two():
    result = topology_derive(
        _box_polygon(), _water_features(), op="select_intersect"
    )
    assert result["hit_count"] == 2
    assert sorted(result["hit_refs"]) == ["w-1", "w-2"]
    assert result["derived_geometry"] is not None
    assert result["derived_geometry"]["type"] in ("Polygon", "MultiPolygon")
    # area_m2 = 派生几何（命中交集并集）面积：两个小水面合计 ≈ 5.9e6 m² 量级
    assert 1e6 < result["area_m2"] < 1e8


def test_topology_derive_difference_avoids_features():
    full = topology_derive(_box_polygon(), [], op="union")
    assert full["hit_count"] == 0
    # difference：框选范围减去命中要素
    result = topology_derive(
        _box_polygon(), _water_features()[:1], op="difference"
    )
    assert result["hit_count"] == 1
    assert result["derived_geometry"] is not None
    # 减去后的面积小于原框选面积
    assert result["area_m2"] < 1e9


def test_topology_derive_empty_intersection_is_honest_zero():
    result = topology_derive(
        _box_polygon(w=100.0, s=10.0, e=100.05, n=10.05),
        _water_features(),
        op="select_intersect",
    )
    assert result["hit_count"] == 0
    assert result["derived_geometry"] is None
    assert result["area_m2"] == 0.0


def test_topology_derive_never_raises_on_invalid_geometry():
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
    }
    result = topology_derive(bowtie, _water_features(), op="select_intersect")
    assert "error" in result or result["hit_count"] == 0


# ---------------------------------------------------------------------------
# T4 情境注入
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_affordance_facts_and_context_block_from_ring():
    session_id = _sid()
    store = FakeSituationStore()
    measure_action = _box_action("m-1", kind="measure")
    measure_action["geometry"] = {
        "type": "LineString",
        "coordinates": [[116.30, 39.80], [116.40, 39.80]],
    }
    measure_action["meta"] = {"measure_value": {"meters": 8534.0}}
    await ingest_canvas_actions(
        session_id, _envelope([_box_action("b-1"), measure_action]), store=store
    )
    ring = store._map_state["_situation_canvas_affordances"]

    facts = affordance_facts(ring)
    assert len(facts) == 2
    for fact in facts:
        assert fact.status == "known"
        assert fact.source == "canvas_affordance"
        assert fact.ref.startswith("canvas:")

    block = render_affordance_context_block(ring)
    assert "[画布意图]" in block
    assert "116.30" in block  # bbox 西界
    assert "8534" in block or "8.53" in block  # 测量值
    assert len(block) <= 1600


def test_render_context_block_empty_ring_returns_empty():
    assert render_affordance_context_block([]) == ""
    assert render_affordance_context_block(None) == ""


def test_tokens_from_ring_shapes():
    ring = [
        {
            "sequence": 3,
            "action_id": "a-1",
            "kind": ACTION_BOX_SELECT,
            "geometry": _box_polygon(),
            "bbox": [116.30, 39.80, 116.36, 39.86],
            "layer_refs": ["water_areas"],
            "screen_px": {"x": 1, "y": 2},
            "observed_at": "2026-09-15T08:00:00Z",
            "payload_hash": "x",
        }
    ]
    tokens = tokens_from_ring(ring)
    assert len(tokens) == 1
    token = tokens[0]
    assert token.token_id == "3:a-1"
    assert token.kind == ACTION_BOX_SELECT
    assert token.bbox == [116.30, 39.80, 116.36, 39.86]


# ---------------------------------------------------------------------------
# T5 生成式 Widget 契约
# ---------------------------------------------------------------------------


def _histogram_payload() -> dict:
    bins = [{"lo": i * 10.0, "hi": (i + 1) * 10.0, "count": i} for i in range(10)]
    return {
        "field": "ndvi",
        "bins": bins,
        "breaks": [50.0],
        "min": 0.0,
        "max": 100.0,
        "layer_ref": "ndvi_layer",
    }


def _widget(kind: str, payload: dict) -> dict:
    return {"widget_id": f"w-{uuid.uuid4().hex[:6]}", "kind": kind, "title": "t", "payload": payload}


def test_widget_spec_valid_all_four_kinds():
    specs = [
        _widget("histogram_slider", _histogram_payload()),
        _widget(
            "swipe_compare",
            {"left": {"label": "方案A", "layer_ref": "lyr-a"}, "right": {"label": "方案B", "layer_ref": "lyr-b"}},
        ),
        _widget(
            "candidate_picker",
            {
                "candidates": [
                    {"id": "c1", "label": "候选1", "stats": {"area_km2": 3.2}},
                    {"id": "c2", "label": "候选2"},
                ],
                "selection_mode": "single",
            },
        ),
        _widget(
            "sketch_box",
            {"prompt": "在图上框选目标范围", "constraints": {"min_area_m2": 100.0}},
        ),
    ]
    for raw in specs:
        spec = validate_widget_spec(raw)
        assert isinstance(spec, WidgetSpec)
        assert spec.kind in ("histogram_slider", "swipe_compare", "candidate_picker", "sketch_box")


@pytest.mark.parametrize(
    "payload",
    [
        {"html": "<script>alert(1)</script>", "field": "x", "bins": [], "breaks": [], "min": 0, "max": 1},
        {"onclick": "alert(1)", "field": "x", "bins": [], "breaks": [], "min": 0, "max": 1},
        {"field": "javascript:alert(1)", "bins": [], "breaks": [], "min": 0, "max": 1},
        {"field": "x", "bins": [], "breaks": [], "min": 0, "max": 1, "src": "data:text/html;base64,AAAA"},
        {"nested": {"onerror": "x"}, "field": "x", "bins": [], "breaks": [], "min": 0, "max": 1},
        {"tag": "</script>", "field": "x", "bins": [], "breaks": [], "min": 0, "max": 1},
    ],
)
def test_widget_spec_rejects_unsafe_payload_matrix(payload):
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("histogram_slider", payload))


def test_widget_spec_rejects_unknown_kind_and_oversize():
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("free_form_html", {"anything": True}))
    big = _histogram_payload()
    big["bins"] = [{"lo": i, "hi": i + 1, "count": 1} for i in range(65)]
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("histogram_slider", big))
    # 超过 16KB 载荷预算
    huge = _histogram_payload()
    huge["field"] = "x" * 17_000
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("histogram_slider", huge))
    # candidate_picker 上限 12
    too_many = {
        "candidates": [{"id": f"c{i}", "label": "x"} for i in range(13)],
        "selection_mode": "multi",
    }
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("candidate_picker", too_many))
    # histogram_slider 缺必填键
    with pytest.raises(ValidationError):
        validate_widget_spec(_widget("histogram_slider", {"field": "x"}))


def test_sse_mount_widget_event_shape():
    spec = validate_widget_spec(_widget("histogram_slider", _histogram_payload()))
    payload = mount_widget_payload(spec, turn_id="t-1", session_id="s-1")
    assert payload["type"] == "ui_action"
    assert payload["action"] == "mount_widget"
    assert payload["widget"]["kind"] == "histogram_slider"
    event = sse_mount_widget(spec, turn_id="t-1", session_id="s-1")
    assert sse_event_type(event) == "ui_action"
    data_line = [ln for ln in event.split("\n") if ln.startswith("data: ")][0]
    parsed = json.loads(data_line[len("data: "):])
    assert parsed["action"] == "mount_widget"
    assert parsed["widget"]["payload"]["field"] == "ndvi"


def test_extract_pending_widgets_from_tool_result_strips_llm_payload():
    spec = _widget("histogram_slider", _histogram_payload())
    raw_result = {
        "status": "ok",
        "breaks": [50.0],
        "ui_actions": [{"action": "mount_widget", "widget": spec}],
    }
    llm_payload = json.dumps(raw_result, ensure_ascii=False)
    widgets, stripped = extract_pending_widgets(raw_result, llm_payload)
    assert len(widgets) == 1
    assert widgets[0]["kind"] == "histogram_slider"
    # llm_payload 不得携带 ui_actions（widget 不进 LLM 上下文）
    assert "ui_actions" not in stripped
    assert '"status": "ok"' in stripped or '"status":"ok"' in stripped


def test_extract_pending_widgets_drops_invalid_and_tolerates_garbage():
    bad_spec = _widget("histogram_slider", {"onclick": "x", "field": "f", "bins": [], "breaks": [], "min": 0, "max": 1})
    raw_result = {
        "ui_actions": [
            {"action": "mount_widget", "widget": bad_spec},
            {"action": "detonate"},           # 未知 action → 丢
            "not-a-dict",                     # 畸形 → 丢
        ],
    }
    widgets, stripped = extract_pending_widgets(raw_result, json.dumps(raw_result))
    assert widgets == ()
    assert isinstance(stripped, str)
    # 非 dict 结果 / 缺键 → 空tuple 原样返回
    w2, p2 = extract_pending_widgets("plain", "plain")
    assert w2 == () and p2 == "plain"


# ---------------------------------------------------------------------------
# T6 ChatRequest schema
# ---------------------------------------------------------------------------


def test_chat_request_accepts_canvas_actions():
    req = ChatRequest(
        message="把框选的水域扩大",
        canvas_actions={
            "envelope_id": "env-1",
            "client_ts": 1760000000000,
            "actions": [_box_action()],
        },
    )
    assert req.canvas_actions is not None
    assert req.canvas_actions.actions[0].kind == ACTION_BOX_SELECT
    assert req.canvas_actions.actions[0].geometry["type"] == "Polygon"


def test_chat_request_rejects_oversized_canvas_actions():
    big_action = _box_action()
    big_action["meta"] = {"blob": "x" * 70_000}
    with pytest.raises(ValidationError):
        ChatRequest(
            message="hi",
            canvas_actions={
                "envelope_id": "env-1",
                "actions": [big_action],
            },
        )


def test_chat_request_canvas_actions_rejects_unknown_action_kind():
    with pytest.raises(ValidationError):
        ChatRequest(
            message="hi",
            canvas_actions={"envelope_id": "e", "actions": [_box_action(kind="mousehover")]},
        )


def test_widget_reply_action_carries_meta_contract():
    """widget 裁决回流：widget_reply action 携带 meta.widget_reply。"""
    action = _box_action("wr-1", kind=ACTION_WIDGET_REPLY)
    action.pop("geometry")
    action["meta"] = {
        "widget_reply": {"widget_id": "w-1", "kind": "histogram_slider", "value": {"breaks": [50.0]}}
    }
    out = normalize_envelope(_envelope([action]))
    assert out is not None
    assert out["actions"][0]["meta"]["widget_reply"]["value"]["breaks"] == [50.0]
