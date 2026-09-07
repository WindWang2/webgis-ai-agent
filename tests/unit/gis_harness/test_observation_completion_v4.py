"""Wave 7 completion-time 观察/验证闭环增量（ADR-0104）回归锁。

覆盖审计 06 确认的缺口修复：
- chart_required 并入 required 槽面（desired + observed + repair 三面共用）；
- 地图模型兼容性完成期复核（组合被绕过时如实披露）；
- 全透明结果层的结构代理检查（像素验证仍归 agent 工具，诚实标签）；
- zoom 形态 viewport 的服务端近似 bbox（extent 检查不再失明）；
- 不确定性披露正证据（owed 义务必须有 chapter 披露证据）；
- task_complete 任务级完成布尔（裁决 + 最终地图状态折叠）。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.completion.contracts import (
    F_LAYER_TRANSPARENT,
    F_MAP_MODEL_MISMATCH,
    MapCompletionResult,
    evaluate_completion_contract,
)
from app.services.gis_harness.completion.map_verification import (
    _check_extent,
    _zoom_viewport_bbox,
)
from app.services.gis_harness.completion.validators.observation import (
    validate_layer_visibility_quality,
    validate_map_model_compat,
)


# ── 全透明结构代理 ───────────────────────────────────────────────────────

def _chapter_with_layers(layers):
    return {
        "plan_id": "p", "query": "q", "recipe_id": "r",
        "map_layers": layers,
        "data_requirements": [], "analysis_steps": [],
    }


def _spec(layers):
    return {"layers": layers, "sources": {}, "layout": {"components": []}}


def test_transparent_result_layer_warned():
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "heatmap", "cartography": "heatmap",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        {"id": "lyr-1", "type": "heatmap", "enabled": True,
         "layout": {"visibility": "visible"},
         "paint": {"heatmap-opacity": 0}},
    ])
    findings = validate_layer_visibility_quality(chapter, mapspec)
    assert len(findings) == 1
    assert findings[0].code == F_LAYER_TRANSPARENT
    assert findings[0].severity == "warning"
    assert "structural proxy" in findings[0].detail


def test_hidden_or_disabled_layer_not_flagged():
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "fill", "cartography": "choropleth",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        {"id": "lyr-1", "type": "fill",
         "layout": {"visibility": "none"}, "paint": {"fill-opacity": 0}},
        {"id": "lyr-1", "type": "fill", "enabled": False, "paint": {"fill-opacity": 0}},
    ])
    assert validate_layer_visibility_quality(chapter, mapspec) == []


def test_transparent_context_layer_not_flagged():
    """非结果层（reference）透明是合法语境弱化 —— 不披露。"""
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "fill", "cartography": "choropleth",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        {"id": "ctx", "type": "fill",
         "layout": {"visibility": "visible"}, "paint": {"fill-opacity": 0}},
        {"id": "lyr-1", "type": "fill",
         "layout": {"visibility": "visible"}, "paint": {"fill-opacity": 0.8}},
    ])
    assert validate_layer_visibility_quality(chapter, mapspec) == []


def test_rgba_alpha_zero_flagged():
    chapter = _chapter_with_layers([
        {"role": "secondary", "layer_type": "fill", "cartography": "choropleth",
         "layer_id": "lyr-2", "bound_ref": "ref:y"},
    ])
    mapspec = _spec([
        {"id": "lyr-2", "type": "fill",
         "layout": {"visibility": "visible"},
         "paint": {"fill-color": "rgba(49, 130, 189, 0)"}},
    ])
    findings = validate_layer_visibility_quality(chapter, mapspec)
    assert [f.code for f in findings] == [F_LAYER_TRANSPARENT]


# ── 地图模型兼容性完成期复核 ─────────────────────────────────────────────

def test_map_model_mismatch_warned_when_bypassed():
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "heatmap", "cartography": "categorical_thematic",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        # categorical_thematic 模型只允许 circle/line/fill —— hexbin 在集外
        {"id": "lyr-1", "type": "hexbin",
         "layout": {"visibility": "visible"}, "paint": {}},
    ])
    findings = validate_map_model_compat(chapter, mapspec)
    assert [f.code for f in findings] == [F_MAP_MODEL_MISMATCH]
    assert "bypassed" in findings[0].detail


def test_map_model_compat_passes_on_permitted_type():
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "heatmap", "cartography": "categorical_thematic",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        {"id": "lyr-1", "type": "fill",
         "layout": {"visibility": "visible"}, "paint": {}},
    ])
    assert validate_map_model_compat(chapter, mapspec) == []


def test_map_model_compat_no_assertion_without_model():
    chapter = _chapter_with_layers([
        {"role": "primary", "layer_type": "fill", "cartography": "",
         "layer_id": "lyr-1", "bound_ref": "ref:x"},
    ])
    mapspec = _spec([
        {"id": "lyr-1", "type": "hexbin", "layout": {}, "paint": {}},
    ])
    assert validate_map_model_compat(chapter, mapspec) == []


# ── zoom 形态 viewport 近似 bbox ─────────────────────────────────────────

def test_zoom_bbox_deterministic_and_sane():
    bbox = _zoom_viewport_bbox([104.06, 30.65], 10)
    assert bbox is not None
    w, s, e, n = bbox
    assert w < 104.06 < e
    assert s < 30.65 < n
    assert bbox == _zoom_viewport_bbox([104.06, 30.65], 10)


def test_zoom_bbox_gross_mismatch_fires_extent_finding():
    result_bbox = [120.0, 25.0, 121.0, 26.0]  # 远离成都
    obs = {"viewport": {"center": [104.06, 30.65], "zoom": 10}}
    findings = _check_extent(result_bbox, obs)
    assert len(findings) == 1
    assert findings[0].code == "result_outside_viewport"
    # 结果在视口内 → 无 finding
    near_bbox = [104.05, 30.60, 104.10, 30.70]
    assert _check_extent(near_bbox, obs) == []


def test_zoom_bbox_rejects_insane_input():
    assert _zoom_viewport_bbox([200.0, 30.0], 10) is None
    assert _zoom_viewport_bbox([104.0, 30.0], 99) is None
    assert _zoom_viewport_bbox(["x", "y"], 10) is None


# ── 不确定性披露正证据 ───────────────────────────────────────────────────

def _result(status="complete"):
    return MapCompletionResult(
        status=status, summary="ok", render_status="verified",
        final_map_status="verified",
    )


def _contract(obligations):
    return {"obligations": obligations, "roles": [],
            "method_blockers": [], "data_blockers": []}


_OWED = [{"obligation_id": "u1", "kind": "uncertainty", "status": "warning",
          "warning_code": "U1"}]
_BLOCKED = [{"obligation_id": "u1", "kind": "uncertainty", "status": "blocked",
             "on_violation": "block_method", "warning_code": "U1"}]


def test_uncertainty_owed_requires_matching_evidence():
    """review R2 MAJOR-6：证据必须匹配 owed 义务码（显式通道或
    methodology_warnings 通道）；不匹配的码不算证据。"""
    result = _result()
    chapter = {"workflow_contract": _contract(_OWED)}
    dims = evaluate_completion_contract(result, [], chapter)["dimensions"]
    assert dims["uncertainty_disclosure"] is False
    # 显式通道：码必须命中 owed
    chapter["uncertainty_disclosures"] = [{"code": "OTHER", "text": "无关"}]
    assert evaluate_completion_contract(result, [], chapter)["dimensions"][
        "uncertainty_disclosure"] is False
    chapter["uncertainty_disclosures"] = [{"code": "U1", "text": "方差 95% CI"}]
    assert evaluate_completion_contract(result, [], chapter)["dimensions"][
        "uncertainty_disclosure"] is True
    # 既有披露通道：methodology_warnings 同码同样算证据
    dims = evaluate_completion_contract(
        result, [{"code": "U1"}], {"workflow_contract": _contract(_OWED)},
    )["dimensions"]
    assert dims["uncertainty_disclosure"] is True


def test_uncertainty_blocked_still_fails_with_evidence():
    result = _result()
    chapter = {"workflow_contract": _contract(_BLOCKED),
               "uncertainty_disclosures": [{"code": "U1", "text": "x"}]}
    dims = evaluate_completion_contract(result, [], chapter)["dimensions"]
    assert dims["uncertainty_disclosure"] is False


def test_uncertainty_no_obligation_passes():
    result = _result()
    dims = evaluate_completion_contract(result, [], {})["dimensions"]
    assert dims["uncertainty_disclosure"] is True


# ── task_complete 任务级完成布尔 ─────────────────────────────────────────

def test_task_complete_fold_from_stored_block():
    from app.services.gis_harness.completion.pipeline import _is_task_complete

    assert _is_task_complete({
        "product_verdict": "READY", "final_map_status": "verified",
    }) is True
    assert _is_task_complete({
        "product_verdict": "READY_WITH_WARNINGS",
        "final_map_status": "verified_with_degradation",
    }) is True
    assert _is_task_complete({
        "product_verdict": "BLOCKED_BY_METHOD", "final_map_status": "verified",
    }) is False
    assert _is_task_complete({
        "product_verdict": "READY", "final_map_status": "unknown",
    }) is False
    assert _is_task_complete({"product_verdict": "NEEDS_REPAIR"}) is False


def test_sse_payload_carries_task_complete():
    from app.services.gis_harness.completion.pipeline import finalization_sse_payload

    ok = MapCompletionResult(
        status="complete", summary="ok", render_status="verified",
        final_map_status="verified", product_verdict="READY",
    )
    payload = finalization_sse_payload(ok, "s1")
    assert payload["task_complete"] is True
    blocked = MapCompletionResult(
        status="complete", summary="ok", render_status="verified",
        final_map_status="verified", product_verdict="BLOCKED_BY_DATA",
    )
    assert finalization_sse_payload(blocked, "s1")["task_complete"] is False


# ── chart_required 槽面并入（async：走 gather_completion_inputs）─────────

@pytest.fixture
async def clean_session():
    sid = f"w7-chart-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_chart_required_extends_required_slots(clean_session):
    from app.services.gis_harness.completion.inputs import gather_completion_inputs

    chapter = {
        "plan_id": "p", "query": "q", "recipe_id": "",
        "template_selection": {"export_profile": {"chart": True}},
        "data_requirements": [], "analysis_steps": [],
        "map_layers": [
            {"role": "primary", "layer_type": "fill", "cartography": "choropleth",
             "layer_id": "lyr-1", "bound_ref": ""},
        ],
    }
    inputs = await gather_completion_inputs(clean_session, chapter)
    assert inputs["facet_contract"].chart_required is True
    chart_slots = [s for s in inputs["required_slots"] if any("chart" in t for t in s)]
    assert chart_slots, "chart_required must join the required slot surface"
