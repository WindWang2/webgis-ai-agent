"""G27 P2 生产接线回归 —— 真实 converter 调用路径（非纯函数自证）。

证据目标：
- A01 旧项目默认渲染逐位不变（无 policy / 空 scale_profile）；
- A02 真实生产路径消费尺度策略，投影进既有 label.zoomBands；
- A06/user-wins：authored zoomBands 不被自动策略覆盖；
- A08 无效 policy 不静默落笔、不阻断出图；
- 投影结果能被 MapSpec schema 原样接受（不新增 spec 字段）。
"""
from __future__ import annotations

import copy

from app.lib.cartography import scale_portrayal as sp
from app.lib.cartography.mapspec_schema import MapSpecLabelZoomBand
from app.services.analysis_cartography_converter import (
    convert_analysis_to_mapspec_layer,
)

V = sp.SCALE_POLICY_VERSION

FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.39, 39.9]},
            "properties": {"name": "a", "population": 10},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.40, 39.91]},
            "properties": {"name": "b", "population": 20},
        },
    ],
}

ANALYSIS = {
    "algorithm": "point_overlay",
    "data": FC,
    "metadata": {"point_count": 2},
    # 固定时点：converter 缺省会取 datetime.now，逐位对比需要确定性 fixture
    "computed_at": "2026-01-01T00:00:00+00:00",
}


def _convert(layer: dict) -> tuple[dict, list[str]]:
    res, _inline, warns = convert_analysis_to_mapspec_layer(
        copy.deepcopy(ANALYSIS), copy.deepcopy(layer))
    return res, warns


def _auto_policy(**extra) -> dict:
    return {"version": V, "mode": "auto_lod", **extra}


def _g27_layer(**extra) -> dict:
    return {
        "id": "L",
        "scale_profile": {"g27_scale_portrayal": _auto_policy(**extra)},
    }


def test_a01_legacy_project_output_is_byte_identical():
    """无 policy 与空 scale_profile 的输出逐位相同 —— 旧图默认渲染不变。"""
    plain, w1 = _convert({"id": "L"})
    empty_profile, w2 = _convert({"id": "L", "scale_profile": {}})
    # _build_layer 原样保留调用方 base_layer：空 scale_profile 是调用方
    # 自己传入的唯一差异，G27 不得新增任何其它字段（默认值零改动）。
    assert {k: v for k, v in empty_profile.items() if k != "scale_profile"} == plain
    assert w1 == w2
    assert not (plain.get("label") or {}).get("zoomBands")


def test_a02_optin_projects_four_bands_through_real_converter_path():
    """真实 converter 落笔 → 既有 spec 字段 label.zoomBands（4 档、全域）。"""
    layer, warns = _convert(_g27_layer())
    bands = (layer.get("label") or {}).get("zoomBands")
    assert bands == sp.spec_label_zoom_bands()
    assert len(bands) == 4
    assert bands[0]["minZoom"] == 0.0
    assert bands[-1]["maxZoom"] == 24.0
    assert "scale_portrayal_projection_skipped" not in " ".join(warns)
    # 投影必须能被现有 schema 原样接受（没有新增 spec 字段）
    for band in bands:
        MapSpecLabelZoomBand(**band)


def test_authored_zoom_bands_win_over_auto_policy():
    """user-wins：显式 authored zoomBands 不被 G27 自动策略覆盖。"""
    authored = [{"minZoom": 0, "maxZoom": 24, "topRatio": 0.42, "sizeRatio": 0.77}]
    layer, _ = _convert({
        "id": "L",
        "label": {"field": "name", "zoomBands": authored},
        "scale_profile": {"g27_scale_portrayal": _auto_policy()},
    })
    assert layer["label"]["zoomBands"] == authored


def test_invalid_policy_does_not_silently_apply():
    """A08：未知 mode / 未知 version 不落笔，且出图不被阻断。"""
    for bad in (
        {"version": V, "mode": "turbo_lod"},
        {"version": "g27-scale-portrayal/0.9.0", "mode": "auto_lod"},
        {"version": V, "mode": "auto_lod", "pinned_size_ratio": 999.0},
    ):
        layer, warns = _convert({
            "id": "L", "scale_profile": {"g27_scale_portrayal": bad},
        })
        assert not (layer.get("label") or {}).get("zoomBands")
        assert layer["id"] == "L" and "paint" in layer  # 出图链未断
        assert "scale_portrayal_projection_skipped" not in " ".join(warns)


def test_disabled_policy_keeps_legacy_behaviour():
    layer, _ = _convert({
        "id": "L",
        "scale_profile": {"g27_scale_portrayal": {"version": V, "mode": "disabled"}},
    })
    assert not (layer.get("label") or {}).get("zoomBands")


def test_policy_is_not_written_as_a_new_spec_field():
    """MapSpec desired state 不新增字段：policy 只在既有开放 dict 内。"""
    layer, _ = _convert(_g27_layer())
    assert "g27_scale_portrayal" not in layer
    assert "scale_portrayal" not in layer
    assert set(layer["scale_profile"]) == {"g27_scale_portrayal"}


def test_f3_converter_and_service_agree_on_priority_field():
    """review F3：同一合法 policy 在两条生产路径的 applied 判定必须一致。

    旧实现 converter 不传 available_fields，带 priority_field 的策略被误判
    幽灵字段而整层不投影；service 传了证据面判为有效 —— 结果相反。
    """
    from app.lib.cartography.scale_portrayal import (
        SCALE_POLICY_NAMESPACE, validate_scale_policy,
    )
    from app.services.gis_harness.scale_portrayal.service import _available_fields

    policy = {"version": V, "mode": "auto_lod",
              "priority_field": "population"}
    spec = {
        "layers": [{"id": "L", "source": "s",
                    "label": {"field": "name"}}],
        "sources": {"s": {"type": "geojson", "inlineData": FC}},
    }
    fields = _available_fields(spec, spec["layers"][0])
    assert "population" in fields
    assert validate_scale_policy(policy, available_fields=fields) == []

    layer, warns = _convert({
        "id": "L",
        "scale_profile": {SCALE_POLICY_NAMESPACE: policy},
    })
    bands = (layer.get("label") or {}).get("zoomBands")
    assert bands == sp.spec_label_zoom_bands()
    assert "scale_portrayal_projection_skipped" not in " ".join(warns)


def test_f11_exception_path_leaves_no_partial_projection():
    """review F11：解析异常被吞成 warning 时，不得留下半投影状态。"""
    from unittest.mock import patch

    with patch(
        "app.lib.cartography.scale_portrayal.resolve_effective_scale_portrayal",
        side_effect=RuntimeError("probe"),
    ):
        layer, warns = _convert(_g27_layer())
    assert not (layer.get("label") or {}).get("zoomBands")
    assert any("scale_portrayal_projection_skipped" in w for w in warns)
    assert layer["id"] == "L" and "paint" in layer  # 出图链未断
