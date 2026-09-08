"""dasymetric 重分配算法 conformance 测试（interpolation.dasymetric）。

契约锁定（descriptor interpolation.dasymetric 的 assumptions/limitations）：
- 精确比例分配：方形单源被两控制面（权重 2:1）切分 → 200/100；
- 总量守恒：逐源 Σ 碎片值 = 源值（浮点舍入前精确）；
- 零权重/缺权重 → 纯面积比例退化（逐源披露，method=area_proportional）；
- 缺字段结构化拒绝（不静默编造）；负值钳 0（计数披露）；
- 确定性：同输入逐位一致（固定累加序：源输入序 × 控制索引序）。

坐标说明：测试面是 WGS84 度数小方块（~1 km），米制面积在自动 UTM 帧
计算 —— 局部尺度因子使面积比例带 ~1e-5 相对偏差（单带投影失真，已记入
descriptor limitations）；权重比例场景下偏差在比值中抵消（≤1e-6）。
"""
from __future__ import annotations

import pytest

from app.lib.geo_analysis.dasymetric import dasymetric_reallocation


def _sq(x0: float, y0: float, x1: float, y1: float) -> dict:
    """WGS84 轴对齐方块（确定性测试面元）。"""
    return {"type": "Polygon", "coordinates": [
        [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _fc(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _poly(fid: str, props: dict, geom: dict) -> dict:
    return {"type": "Feature", "id": fid, "properties": props, "geometry": geom}


# 经典用例：单位方源 (0,0)-(0.01,0.01)，左/右两控制面对半切
_SOURCE_SQUARE = _poly(
    "S1", {"id": "S1", "pop": 300.0}, _sq(0.0, 0.0, 0.01, 0.01))
_ANC_TWO_ZONES = _fc(
    _poly("z-l", {"z": "left", "w": 2.0}, _sq(0.0, 0.0, 0.005, 0.01)),
    _poly("z-r", {"z": "right", "w": 1.0}, _sq(0.005, 0.0, 0.01, 0.01)),
)


def test_exact_proportional_split_on_square():
    """权重 2:1 的两控制面 → 源值 300 精确切分为 200/100。"""
    res = dasymetric_reallocation(
        _fc(_SOURCE_SQUARE), _ANC_TWO_ZONES, "pop", weight_field="w")
    assert res.success, res.summary
    frags = res.data["features"]
    assert len(frags) == 2
    by_anc = {f["properties"]["ancillary_id"]: f["properties"]["pop"]
              for f in frags}
    (left, right) = (by_anc["0"], by_anc["1"])
    assert left == pytest.approx(200.0, rel=1e-6)
    assert right == pytest.approx(100.0, rel=1e-6)
    # 份额列与值一致（share × 300 = value）
    shares = {f["properties"]["ancillary_id"]: f["properties"]["value_share"]
              for f in frags}
    assert shares["0"] == pytest.approx(2 / 3, abs=1e-6)
    assert shares["1"] == pytest.approx(1 / 3, abs=1e-6)
    # 方法标签：权重生效 → ancillary_weighted
    assert all(f["properties"]["method"] == "ancillary_weighted"
               for f in frags)
    assert res.data["metadata"]["weight_applied"] is True


def test_mass_conservation_and_fragment_count():
    """多源 × 多控制面：逐源 Σ 碎片值 = 源值；碎片数 = 非空相交数。"""
    sources = _fc(
        _poly("A", {"id": "A", "pop": 100.0}, _sq(0.0, 0.0, 0.004, 0.01)),
        _poly("B", {"id": "B", "pop": 60.0}, _sq(0.004, 0.0, 0.009, 0.01)),
    )
    res = dasymetric_reallocation(sources, _ANC_TWO_ZONES, "pop",
                                  weight_field="w")
    assert res.success, res.summary
    per_source: dict = {}
    for f in res.data["features"]:
        sid = f["properties"]["source_id"]
        per_source[sid] = per_source.get(sid, 0.0) + f["properties"]["pop"]
    assert per_source["A"] == pytest.approx(100.0, rel=1e-6)
    assert per_source["B"] == pytest.approx(60.0, rel=1e-6)
    # B 与两个控制面都相交 → 2 碎片；A 完全落在左控制面 → 1 碎片
    counts: dict = {}
    for f in res.data["features"]:
        counts[f["properties"]["source_id"]] = (
            counts.get(f["properties"]["source_id"], 0) + 1)
    assert counts == {"A": 1, "B": 2}
    assert res.data["metadata"]["mass_conserving"] is True


def test_zero_weight_falls_back_to_area_proportional():
    """权重全零（有覆盖）→ 面积比例退化：对半切 → 150/150，逐源披露。"""
    zero_weights = _fc(
        _poly("z-l", {"z": "left", "w": 0.0}, _sq(0.0, 0.0, 0.005, 0.01)),
        _poly("z-r", {"z": "right", "w": 0.0}, _sq(0.005, 0.0, 0.01, 0.01)),
    )
    res = dasymetric_reallocation(
        _fc(_SOURCE_SQUARE), zero_weights, "pop", weight_field="w")
    assert res.success, res.summary
    frags = res.data["features"]
    vals = sorted(f["properties"]["pop"] for f in frags)
    assert vals[0] == pytest.approx(150.0, rel=1e-4)
    assert vals[1] == pytest.approx(150.0, rel=1e-4)
    assert all(f["properties"]["method"] == "area_proportional"
               for f in frags)
    assert res.data["metadata"]["area_proportional_fallback"] == 1
    # 权重字段缺失（未传 weight_field）→ 全局面比例，全局披露
    res2 = dasymetric_reallocation(_fc(_SOURCE_SQUARE), zero_weights, "pop")
    assert res2.success
    assert res2.data["metadata"]["weight_applied"] is False
    assert res2.data["metadata"]["method"] == "area_proportional"


def test_uncovered_source_keeps_value_whole():
    """控制层未覆盖的源面 → 整面保值（no_ancillary_coverage），不丢值。"""
    sources = _fc(
        _poly("covered", {"id": "covered", "pop": 100.0},
              _sq(0.0, 0.0, 0.004, 0.01)),
        _poly("gap", {"id": "gap", "pop": 50.0},
              _sq(0.02, 0.0, 0.03, 0.01)),
    )
    res = dasymetric_reallocation(sources, _ANC_TWO_ZONES, "pop",
                                  weight_field="w")
    assert res.success
    gap = [f for f in res.data["features"]
           if f["properties"]["source_id"] == "gap"]
    assert len(gap) == 1
    assert gap[0]["properties"]["pop"] == pytest.approx(50.0)
    assert gap[0]["properties"]["method"] == "no_ancillary_coverage"
    assert res.data["metadata"]["uncovered_sources"] == 1


def test_missing_value_field_rejected():
    """值字段缺失 → 结构化拒绝（附可用字段提示），不编造结果。"""
    res = dasymetric_reallocation(
        _fc(_SOURCE_SQUARE), _ANC_TWO_ZONES, "no_such_field")
    assert not res.success
    assert res.error_type == "ValueError"
    assert "no_such_field" in res.summary
    assert res.correction_hint  # 自愈提示在场


def test_empty_and_invalid_inputs_rejected():
    """空/坏输入结构化拒绝：空控制层 → 拒绝并提示改用 choropleth。"""
    empty_fc = _fc()
    for args in (
        {"source_geojson": empty_fc, "ancillary_geojson": _ANC_TWO_ZONES},
        {"source_geojson": _fc(_SOURCE_SQUARE), "ancillary_geojson": empty_fc},
    ):
        res = dasymetric_reallocation(
            args["source_geojson"], args["ancillary_geojson"], "pop")
        assert not res.success
        assert res.error_type == "ValueError"


def test_negative_values_clamped():
    """负总量钳 0（负值无密度语义），钳制计数逐法披露。"""
    neg = _poly("N", {"id": "N", "pop": -300.0}, _sq(0.0, 0.0, 0.01, 0.01))
    res = dasymetric_reallocation(_fc(neg), _ANC_TWO_ZONES, "pop",
                                  weight_field="w")
    assert res.success
    for f in res.data["features"]:
        assert f["properties"]["pop"] == 0.0
    assert res.data["metadata"]["clamped_negative_values"] == 1


def test_negative_weights_clamped():
    """控制权重负值钳 0（2:−1 场景不产生负分配）。"""
    bad_weights = _fc(
        _poly("z-l", {"z": "left", "w": 2.0}, _sq(0.0, 0.0, 0.005, 0.01)),
        _poly("z-r", {"z": "right", "w": -1.0}, _sq(0.005, 0.0, 0.01, 0.01)),
    )
    res = dasymetric_reallocation(
        _fc(_SOURCE_SQUARE), bad_weights, "pop", weight_field="w")
    assert res.success
    assert res.data["metadata"]["clamped_weights"] == 1
    # 全部分配归左（唯一正权重面）
    left = [f for f in res.data["features"]
            if f["properties"]["ancillary_id"] == "0"]
    assert left[0]["properties"]["pop"] == pytest.approx(300.0, rel=1e-6)


def test_deterministic_repeat():
    """确定性：同输入两次运行逐位一致（无 RNG、固定累加序）。"""
    sources = _fc(
        _SOURCE_SQUARE,
        _poly("B", {"id": "B", "pop": 60.0}, _sq(0.004, 0.0, 0.009, 0.01)),
    )
    first = dasymetric_reallocation(sources, _ANC_TWO_ZONES, "pop",
                                    weight_field="w")
    second = dasymetric_reallocation(sources, _ANC_TWO_ZONES, "pop",
                                     weight_field="w")
    assert first.success and second.success
    assert first.data["features"] == second.data["features"]
    assert first.data["metadata"] == second.data["metadata"]


# ── 注册表/工具接线（dasymetric_map 原生化的门禁）─────────────────────


class TestDasymetricRegistryWiring:
    """模型 native、算法/能力/工具/契约全链在册且交叉引用一致。"""

    def test_model_promoted_to_native(self):
        from app.lib.cartography.model_library import (
            get_map_model_registry,
            validate_model_library,
        )

        reg = get_map_model_registry()
        assert "dasymetric_map" in reg.native_ids()
        assert "dasymetric_map" not in reg.planned_ids()
        model = reg.resolve("dasymetric_map")
        assert model is not None
        # native 模型图层必须在前端运行时支持族内（fill ∈ 词表）
        assert model.maplibre_layer_type == "fill"
        # 降级链仍可解析（无控制层 → 归一化分级统计图）
        assert model.fallback_model_id == "normalized_choropleth"
        assert validate_model_library() == []

    def test_algorithm_capability_tool_contract_chain(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.capability_registry import get_capability_registry

        algo = get_algorithm_registry().get("interpolation.dasymetric")
        assert algo is not None
        assert algo.runtime_status == "native"
        assert "dasymetric_map" in algo.compatible_map_models
        assert algo.tool_candidates == ["dasymetric_reallocation"]
        assert get_capability_registry().has("areal_interpolation")

    def test_tool_registered_and_schema_matches_contract(self):
        from app.tools.dasymetric_tools import register_dasymetric_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_dasymetric_tools(reg)
        assert "dasymetric_reallocation" in set(reg.list_tools())
        schemas = {s["function"]["name"]: s["function"].get("parameters") or {}
                   for s in reg.get_schemas()}
        props = set((schemas["dasymetric_reallocation"]
                     .get("properties") or {}).keys())
        # 契约 required 参数必须在工具 schema 内（§43 parity 门语义）
        for name in ("source_geojson", "ancillary_geojson", "value_field"):
            assert name in props

    async def test_registry_validation_tool_dispatch_end_to_end(self):
        """垂直切片：工具派发产出 dasymetric_map 渲染契约（generic 链入口）。"""
        from app.tools.dasymetric_tools import register_dasymetric_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_dasymetric_tools(reg)
        res = await reg.dispatch("dasymetric_reallocation", {
            "source_geojson": _fc(_SOURCE_SQUARE),
            "ancillary_geojson": _ANC_TWO_ZONES,
            "value_field": "pop",
            "weight_field": "w",
        })
        assert isinstance(res, dict) and res.get("success") is not False
        # 渲染接线契约（generic 分级面链的入口）
        assert res["type_hint"] == "dasymetric_map"
        assert res["command"] == "add_layer"
        # Review R1（GIS F12）：工具不再强制 continuous legend_spec ——
        # dasymetric_map 是 graduated 模型，图例由模型分级链派生。
        assert "legend_spec" not in res
        assert res["scientific_evidence"]  # 证据块在册
