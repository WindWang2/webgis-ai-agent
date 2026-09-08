"""Harness V5 rendered-state observation 契约（ADR-0118 D5）。

验收锚点（Epic V5）：地图 finalization 能检测请求层与实际渲染状态不
一致 ——
- per-layer telemetry：source_status=error / render_complete=False /
  style_converged=False / feature_count=0 的 typed findings（全部
  optional 门控，旧客户端零新 finding）；
- chart_required 数据级核验：组件在场但无 rendered+data_points>0 →
  error；telemetry 缺席 → 诚实 warning（不假通过）；
- 全部一致 → verified（不引入新门）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


from app.services.gis_harness.completion.contracts import (
    F_CHART_DATA_MISSING,
    F_RENDER_INCOMPLETE,
    F_RENDER_LAYER_MISSING,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_STYLE_NOT_APPLIED,
    RUNTIME_RENDER_CODES,
)
from app.services.gis_harness.render_observation import (
    validate_render_observation,
)


def _chapter(layer_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "map_layers": [
            {"layer_id": lid, "role": "result", "enabled": True,
             "visible": True}
            for lid in (layer_ids or ["product-heat"])
        ],
    }


def _mapspec(layer_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "layers": [
            {"id": lid, "type": "circle", "source": f"src-{lid}",
             "paint": {}, "layout": {"visibility": "visible"}}
            for lid in (layer_ids or ["product-heat"])
        ],
    }


def _observation(layer_ids: Optional[List[str]] = None,
                 **entry_extra: Any) -> Dict[str, Any]:
    return {
        "source": "frontend_runtime",
        "sequence": 1,
        "mapspec_revision": 3,
        "layers": [
            {"id": lid, "runtime_store_id": lid, "visible": True,
             "runtime_layer_count": 1, "source_converged": True,
             **entry_extra}
            for lid in (layer_ids or ["product-heat"])
        ],
        "components": [{"id": "t1", "type": "title", "mounted": True}],
    }


REV = 3


def test_baseline_no_telemetry_still_verified():
    """旧客户端观察（无新字段）→ 维持 V4 语义 verified，零新 finding。"""
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), _observation(), REV, [["title"]])
    assert status == "verified"
    assert findings == []


def test_source_error_is_typed_error_finding():
    obs = _observation(source_status="error")
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [])
    assert status == "issues"
    codes = {(f.code, f.severity) for f in findings}
    assert (F_RENDER_SOURCE_MISSING, "error") in codes


def test_render_incomplete_is_error():
    obs = _observation(render_complete=False)
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [])
    assert status == "issues"
    assert any(f.code == F_RENDER_INCOMPLETE and f.severity == "error"
               for f in findings)


def test_style_not_applied_is_warning():
    obs = _observation(style_converged=False)
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [])
    assert status == "verified"  # warning 不推翻
    assert any(f.code == F_RENDER_STYLE_NOT_APPLIED and f.severity == "warning"
               for f in findings)


def test_zero_feature_count_is_warning_disclosure():
    obs = _observation(feature_count=0)
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [])
    assert status == "verified"
    assert any(f.code == F_RENDER_INCOMPLETE and f.severity == "warning"
               for f in findings)


# ---------------------------------------------------------------- chart 校验

def _with_chart_slot(obs: Dict[str, Any], mounted: bool = True) -> Dict[str, Any]:
    obs["components"] = [{"id": "chart-1", "type": "chart_panel",
                          "mounted": mounted}]
    return obs


def test_chart_required_with_rendered_data_passes():
    obs = _with_chart_slot(_observation())
    obs["charts"] = [{"id": "chart-1", "rendered": True, "data_points": 12}]
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [["chart_panel"]])
    assert status == "verified"
    assert not any(f.code == F_CHART_DATA_MISSING for f in findings)


def test_chart_required_mounted_but_no_data_is_error():
    """requested(chart with data) ↔ actual(0 系列) —— 硬分歧 error。"""
    obs = _with_chart_slot(_observation())
    obs["charts"] = [{"id": "chart-1", "rendered": False, "data_points": 0}]
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [["chart_panel"]])
    assert status == "issues"
    assert any(f.code == F_CHART_DATA_MISSING and f.severity == "error"
               for f in findings)


def test_chart_required_telemetry_absent_is_honest_warning():
    """旧客户端无 charts 键 → warning 披露（槽级校验兜底），不误判 error。"""
    obs = _with_chart_slot(_observation())
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, REV, [["chart_panel"]])
    assert status == "verified"
    chart_findings = [f for f in findings if f.code == F_CHART_DATA_MISSING]
    assert chart_findings and chart_findings[0].severity == "warning"


def test_new_runtime_codes_in_needs_repair_vocabulary():
    """新码进 runtime-render 词表（自愈语义 → needs_repair 而非 failed）。"""
    for code in (F_RENDER_INCOMPLETE, F_RENDER_STYLE_NOT_APPLIED,
                 F_CHART_DATA_MISSING):
        assert code in RUNTIME_RENDER_CODES


def test_full_mismatch_detection_scenario():
    """端到端：请求 2 层，实际 1 层挂载+源错误+1 图表无数据 → 全检出。"""
    chapter = _chapter(["product-heat", "product-points"])
    mapspec = _mapspec(["product-heat", "product-points"])
    obs = _observation(["product-heat"], source_status="loaded",
                       render_complete=True)
    obs["layers"][0]["runtime_layer_count"] = 1
    obs["components"] = [{"id": "c1", "type": "chart_panel", "mounted": True}]
    obs["charts"] = [{"id": "c1", "rendered": False, "data_points": 0}]
    status, findings = validate_render_observation(
        chapter, mapspec, obs, REV, [["chart_panel"]])
    assert status == "issues"
    codes = {f.code for f in findings}
    assert F_RENDER_LAYER_MISSING in codes      # product-points 整层缺席
    assert F_CHART_DATA_MISSING in codes        # 图表无数据
