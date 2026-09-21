"""create_thematic_map × Measurement 生产热路径接线测试（ADR-0204）。

验收矩阵对应：A13（缺省参数下与 master 行为一致的回归锁）、A1/A6/A7 的
工具级证据、unit 自动填充与 user-wins、display_hints 下发。

注意：ThematicMapArgs.k 曾有「尾逗号使缺省值成 (FieldInfo,) 元组」的
master 缺陷 —— 只要 LLM 省略 k，自动裁决路径必抛校验错误（本 PR 修复）。
test_auto_adjudication_path_reachable_with_k_omitted 锁定该修复。
"""
import pytest

from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


def _fc(props_list, geom="Polygon"):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": geom,
                    "coordinates": (
                        [[[0, 0], [1, 0], [1, 1], [0, 0]]] if geom == "Polygon"
                        else [i * 0.01, i * 0.01]
                    ),
                },
                "properties": props,
            }
            for i, props in enumerate(props_list)
        ],
    }


async def test_explicit_method_regression_unchanged(registry):
    """A13：显式 method/k 路径行为与 master 一致（graduated + 图例）。"""
    gj = _fc([{"pop": 10.0 + i * 90.0} for i in range(3)], geom="Point")
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "pop", "method": "equal_interval", "k": 3,
    })
    assert "error" not in out
    assert out["legend_spec"]["type"] == "graduated"
    assert out["legend_spec"]["field"] == "pop"
    assert out["layer_meta"]["title"] == "pop 专题图"


async def test_auto_adjudication_path_reachable_with_k_omitted(registry):
    """master 缺陷回归锁：省略 k 时自动裁决可达（不再抛 FieldInfo 校验错）。"""
    gj = _fc([{"v": float(10 + i * 37)} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "v"})
    assert "error" not in out, out.get("error")
    assert "legend_spec" in out
    assert out["symbology_decision"]["method"] in (
        "quantiles", "equal_interval", "natural_breaks", "head_tail",
    )


async def test_signed_change_builds_divergent_with_center_zero(registry):
    """A6 工具级：跨 0 的变化字段 → divergent + center 0 + 图例。"""
    gj = _fc([{"growth": -3.5 + i * 2.0} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "growth"})
    assert "error" not in out
    ls = out["legend_spec"]
    assert ls["type"] == "divergent"
    assert ls["center"] == 0.0
    assert out["style"]["type"] == "divergent"


async def test_all_positive_change_falls_back_to_graduated(registry):
    """全正『变化率』无符号结构 → 留在 graduated（不虚构符号编码）。"""
    gj = _fc([{"growth": 1.0 + i * 0.5} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "growth"})
    assert out["legend_spec"]["type"] == "graduated"


async def test_legend_unit_autofill_percent_and_user_wins(registry):
    """图例单位：fraction 语义自动填充；显式 unit 恒 user-wins。"""
    gj = _fc([{"绿地率": 0.1 + i * 0.05} for i in range(10)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "绿地率"})
    assert out["legend_spec"]["unit"] == "比例"
    out2 = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "绿地率", "unit": "百分比",
    })
    assert out2["legend_spec"]["unit"] == "百分比"


async def test_population_unit_autofill(registry):
    gj = _fc([{"pop": 100.0 + i * 50.0} for i in range(10)], geom="Point")
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "pop"})
    assert out["legend_spec"]["unit"] == "人"


async def test_degree_like_metric_check_surfaces(registry):
    """A1 工具级：地理 CRS（RFC 7946 默认）度级值冒充米制 → 证据码下发。"""
    gj = _fc([{"dist_m": 12.3 + i * 3.7} for i in range(10)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "dist_m"})
    checks = out["layer_meta"].get("measurement_checks") or []
    assert "DEGREE_LIKE_METRIC" in [c["code"] for c in checks]


async def test_display_hints_surfaced_in_layer_meta(registry):
    """S5 接线：multipart/高密度提示随 layer_meta 下发。"""
    gj = _fc(
        [{"v": float(i)} for i in range(12)], geom="Polygon",
    )
    # 构造 multipart 证据。
    gj["features"][0]["geometry"] = {
        "type": "MultiPolygon",
        "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]],
    }
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "v"})
    hints = out["layer_meta"].get("display_hints") or []
    assert "MULTIPART_GEOMETRY" in [h["code"] for h in hints]


async def test_semantic_profile_param_consumed(registry):
    """semantic_profile 入参被消费：category 语义 → categorical 模式。"""
    gj = _fc([{"zone": float(i % 3)} for i in range(9)])
    sp = {
        "field_roles": [
            {"field": "zone", "roles": ["category"],
             "confidence": "rule_derived", "evidence": ["value_sample"]},
        ],
        "role_index": {"category": "zone"},
    }
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "zone", "semantic_profile": sp,
    })
    assert out["classification_plan"]["method"] == "categorical"
    assert out["classification_plan"]["source"] == "semantic"


async def test_invalid_semantic_profile_fail_soft(registry):
    """坏 semantic_profile → 忽略并照常出图（fail-soft，不阻断）。"""
    gj = _fc([{"v": float(10 + i * 7)} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "v", "semantic_profile": {"bogus": True},
    })
    assert "error" not in out
    assert "legend_spec" in out


async def test_divergent_path_annotates_classification_plan(registry):
    """review P2：实图 divergent 时 classification_plan 显式标注表达。"""
    gj = _fc([{"growth": -3.5 + i * 2.0} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {"geojson": gj, "field": "growth"})
    assert out["classification_plan"]["expression"] == "divergent"


async def test_lisa_path_keeps_unit(registry):
    """review P3：lisa 模式下 unit 参数不静默失效。"""
    gj = _fc([{"v": float(10 + i * 7), "cat": str(i % 3)} for i in range(12)])
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "v", "method": "lisa", "unit": "人",
    })
    ls = out.get("legend_spec")
    if ls is not None:
        assert ls.get("unit") == "人"
