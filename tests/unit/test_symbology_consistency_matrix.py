"""AC-03 P7 一致性回归：同一数据 × 多入口 → 同一 SymbologyDecision。

任务书 §5 门禁 1：6 个标准数据集 × 5 个入口产出**同一** SymbologyDecision
（source 字段差异允许）。入口：build_thematic_style / create_thematic_map /
h3_binning / heatmap_data / apply_template。

h3_binning 的分类对象是**网格聚合值**（不是原始点值）——矩阵中给每个点
独立坐标（各自落入单独 H3 单元）+ stat_method='sum'，使网格值 ≡ 数据集值；
heatmap_data 的 native 守卫只接受点要素（多边形网格会被确定性拒绝），其
裁决走工具同款 ``_adjudicate_heatmap_palette``（与工具共享实现），语义
字段（method/k/palette/clip_policy/context）必须与其它入口一致——热力图
的裁决理由含显式色带记录，故逐字段比对而非全字典比对。
"""
import pytest

import app.services.templates.intent_resolver as _intent_resolver
from app.lib.cartography.symbology import symbology_decision_from_values
from app.services.cartography_service import CartographyService

FIELD = "v"

HEAVY = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 13.0,
         20.0, 40.0, 80.0, 160.0, 320.0, 640.0, 1280.0, 5000.0]
UNIFORM = [float(i) for i in range(10, 210, 2)]
MID_SKEW = [10.0, 12.0, 14.0, 11.0, 13.0, 15.0, 12.0, 14.0, 16.0, 13.0,
            11.0, 15.0, 14.0, 12.0, 17.0, 16.0, 13.0, 18.0, 15.0, 14.0]
CONSTANT = [7.0] * 20
# 中等离群：1195 个低值 + 5 个 100（尖峰 <1% 使 p99 停在低值段；
# mean/median 比未达重尾阈值，但 max ≫ p99 → clip_p99）
SPIKE = [float(i % 10 + 1) for i in range(1195)] + [100.0] * 5
SMALL = [3.0, 1.0, 4.0, 1.5, 2.5]

DATASETS = {
    "heavy_tail": HEAVY,
    "near_uniform": UNIFORM,
    "medium_skew": MID_SKEW,
    "constant": CONSTANT,
    "moderate_spike": SPIKE,
    "small_n": SMALL,
}


def _polygon_fc(values):
    features = [
        {"type": "Feature", "properties": {FIELD: v},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}
        for v in values
    ]
    return {"type": "FeatureCollection", "features": features}


def _points_fc(values):
    """紧凑 2D 网格布点（0.05° 间距 ≫ res-8 单元尺寸，且远离 ±180° 环绕）。"""
    features = []
    for i, v in enumerate(values):
        lng = -122.0 + (i % 40) * 0.05
        lat = 37.0 + (i // 40) * 0.05
        features.append({
            "type": "Feature", "properties": {FIELD: v},
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
        })
    return {"type": "FeatureCollection", "features": features}


NEUTRAL_TEMPLATE = {
    "id": "tmpl_ac03_neutral_probe",
    "kind": "thematic",
    "name": "AC-03 一致性中性探针（无偏好）",
    "category": "thematic",
    "payload": {"variant": "choropleth"},
    "is_builtin": True,
    "version": 1,
}


@pytest.fixture()
def neutral_template(monkeypatch):
    monkeypatch.setattr(
        _intent_resolver, "get_template_or_composite", lambda tid: (
            dict(NEUTRAL_TEMPLATE) if tid == NEUTRAL_TEMPLATE["id"] else None
        ), raising=True)
    return NEUTRAL_TEMPLATE["id"]


def _decisions_for(values):
    """同一数据跑 5 个入口，返回 {entry: decision_dict}。"""
    import asyncio

    from app.tools.registry import ToolRegistry
    from app.tools.templates import register_template_tools

    fc = _polygon_fc(values)
    pts = _points_fc(values)

    out = {}

    # 入口 1：CartographyService.build_thematic_style 直调
    style = CartographyService.build_thematic_style(fc, FIELD)
    out["build_thematic_style"] = style["symbology_decision"]

    # 入口 2：create_thematic_map（method/k/palette 全缺省 → 全裁决）
    from app.tools.cartography import register_cartography_tools
    from app.tools.registry import ToolRegistry as _TR
    _reg = _TR()
    register_cartography_tools(_reg)
    res = _reg._tools["create_thematic_map"](geojson=fc, field=FIELD)
    assert "error" not in res, res
    out["create_thematic_map"] = res["symbology_decision"]

    # 入口 3：h3_binning（点 → 网格；独立坐标使网格 sum ≡ 原始值）
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    _reg2 = _TR()
    register_advanced_spatial_tools(_reg2)
    h3res = _reg2._tools["h3_binning"](
        geojson=pts, resolution=8, stat_field=FIELD, stat_method="sum")
    grid = h3res.get("data")
    assert isinstance(grid, dict) and grid.get("features"), h3res.get("error")
    grid_vals = [f["properties"]["sum"] for f in grid["features"]]
    assert sorted(grid_vals) == sorted(values), "H3 网格聚合必须无损（每点独立单元）"
    out["h3_binning"] = h3res["symbology_decision"]
    # 交叉验证：其余入口吃同一份网格输出也必须是同一决策
    style_on_grid = CartographyService.build_thematic_style(grid, "sum")
    assert style_on_grid["symbology_decision"] == h3res["symbology_decision"]

    # 入口 4：heatmap_data 的裁决实现（工具共享函数；native 守卫拒绝多边形，
    # 端到端等价性由共享实现保证）
    from app.tools.spatial import _adjudicate_heatmap_palette
    _family, heat_dec = _adjudicate_heatmap_palette("classic", grid, "sum", "screen")
    assert _family == "classic"  # screen 上下文恒等映射（默认渲染零变化）
    out["heatmap_data"] = heat_dec.to_dict()

    # 入口 5：apply_template（中性探针模板：无任何偏好声明；走 registry 同契约测试）
    reg = ToolRegistry()
    register_template_tools(reg)
    res5 = asyncio.run(reg.dispatch("apply_template", {
        "template_id": NEUTRAL_TEMPLATE["id"], "geojson": grid, "field": "sum",
    }))
    assert "error" not in res5, res5
    out["apply_template"] = res5["symbology_decision"]

    return out, grid


# 全字典相等的入口（同值、同 intent 提取）：
_FULL_EQ_ENTRIES = ["build_thematic_style", "create_thematic_map",
                    "h3_binning", "apply_template"]
# 语义字段相等的入口（heatmap 理由含显式色带记录，属 source/reasons 差异）
_SEMANTIC_KEYS = ["method", "k", "palette", "clip_policy", "context",
                  "confidence", "low_confidence"]


@pytest.mark.parametrize("name", list(DATASETS))
def test_six_datasets_five_entries_same_decision(name, neutral_template):
    values = DATASETS[name]
    decisions, _grid = _decisions_for(values)

    base = decisions["build_thematic_style"]
    for entry in _FULL_EQ_ENTRIES[1:]:
        assert decisions[entry] == base, (
            f"{name}/{entry} 与 build_thematic_style 决策不一致:\n"
            f"  base={base}\n  actual={decisions[entry]}"
        )
    for entry, dec in decisions.items():
        for key in _SEMANTIC_KEYS:
            assert dec[key] == base[key], (
                f"{name}/{entry} 语义字段 {key} 不一致: {dec[key]} != {base[key]}"
            )


def test_expected_adjudication_per_dataset():
    """6 个数据集的期望裁决（锁定分布驱动行为本身，不只是入口一致性）。"""
    expected = {
        "heavy_tail": ("head_tail", "YlOrRd", "head_tail"),
        "near_uniform": ("equal_interval", "YlOrRd", "none"),
        "medium_skew": ("natural_breaks", "YlOrRd", "none"),
        "constant": ("equal_interval", "YlOrRd", "none"),   # low_confidence
        # 低值主体 1..10 循环本身近均匀 + 少量 100 尖峰：近均匀裁决 + clip_p99 披露
        "moderate_spike": ("equal_interval", "YlOrRd", "clip_p99"),
        "small_n": ("equal_interval", "YlOrRd", "none"),    # low_confidence, k=3
    }
    for name, (method, palette, clip) in expected.items():
        d = symbology_decision_from_values(DATASETS[name])
        assert d.method == method, name
        assert d.palette == palette, name
        assert d.clip_policy == clip, name
    assert symbology_decision_from_values(DATASETS["constant"]).low_confidence
    assert symbology_decision_from_values(DATASETS["small_n"]).k == 3


def test_out_of_range_disclosed_when_clipped(neutral_template):
    """§5 门禁 6：离群裁剪 100% 在 legend 明示（out_of_range 条目存在）。"""
    import asyncio

    from app.tools.registry import ToolRegistry
    from app.tools.templates import register_template_tools
    from app.tools.cartography import register_cartography_tools

    values = DATASETS["moderate_spike"]
    grid_like = _polygon_fc(values)
    reg = ToolRegistry()
    register_cartography_tools(reg)
    res = reg._tools["create_thematic_map"](geojson=grid_like, field=FIELD)
    spec = res["legend_spec"]
    assert spec["clip_policy"] == "clip_p99"
    assert "out_of_range" in spec and spec["out_of_range"]["count"] >= 1
    assert spec["out_of_range_label"]

    reg2 = ToolRegistry()
    register_template_tools(reg2)
    res5 = asyncio.run(reg2.dispatch("apply_template", {
        "template_id": NEUTRAL_TEMPLATE["id"], "geojson": grid_like, "field": FIELD,
    }))
    spec5 = res5["legend_spec"]
    assert spec5["clip_policy"] == "clip_p99"
    assert "out_of_range" in spec5
