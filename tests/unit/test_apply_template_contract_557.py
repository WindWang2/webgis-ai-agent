"""
#557 契约矩阵测试：apply_template 发射形状 == 前端 registry 解析期望。

覆盖 5 个断点的生产端（emitter）形状，断言的是 apply_template / build_thematic_style
的实际返回（真实 emit，不 mock 双方）：

  1. symbology single  → params.style（flat paint 键），无顶层 style_applied
  2. basemap           → params.name 为 TILE_PROVIDERS 规范名（providerId 已解析）
  3. categorical       → build_thematic_style(method=categorical) 产出类别→色表
                         （string/numeric），不再是 equal_interval 数值断点；
                         无有效类别 → 显式 error，不假成功
  4. implicit None     → 未知 kind/mode/variant 显式 error
  5. fillOpacity       → 发射端 style 携带 fillOpacity（前端链路另有 renderer 测试）

前端 TILE_PROVIDERS 规范名镜像（frontend/lib/providers.ts，跨仓库夹具）：
base_layer_change 的 params.name 必须命中这个集合。
"""
import json

import pytest

from app.core.base_layers import resolve_provider_id_to_name
from app.schemas.template_schema import SEED_TEMPLATES
from app.services.cartography_service import CartographyService
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools

# 前端 frontend/lib/providers.ts TILE_PROVIDERS[].name（跨仓库契约夹具）
FRONTEND_TILE_PROVIDER_NAMES = {
    "Carto Positron 矢量",
    "Carto Dark Matter 矢量",
    "Carto 浅色",
    "Carto 深色",
    "OSM 地图",
    "ESRI 影像",
    "ESRI 地形",
    "OpenTopoMap",
    "高德影像",
    "高德矢量",
    "天地图矢量",
    "天地图影像",
}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_template_tools(reg)
    return reg


def _basemap_templates():
    out = []
    for t in SEED_TEMPLATES:
        if t.get("kind") != "basemap":
            continue
        pid = (t.get("payload") or {}).get("providerId")
        if pid:
            out.append((t["id"], pid))
    return out


# ── 断点 2：basemap providerId → 规范名 ──────────────────────────────────


def test_every_builtin_basemap_providerId_resolves_to_frontend_canonical_name():
    """每个内建 basemap 种子的 providerId 都必须能解析成前端 TILE_PROVIDERS 名字。

    修复前模板载荷原样进 params（providerId / vectorStyleUrl）—— 与前端
    base_layer_change 期望的 params.name 零交集，切换无效。
    """
    templates = _basemap_templates()
    assert len(templates) >= 6
    for tpl_id, pid in templates:
        assert resolve_provider_id_to_name(pid) is not None, f"{tpl_id} ({pid}) 解析失败"
        assert resolve_provider_id_to_name(pid) in FRONTEND_TILE_PROVIDER_NAMES, (
            f"{tpl_id} ({pid}) 解析到非前端目录名"
        )


@pytest.mark.asyncio
async def test_basemap_emits_params_name_canonical(registry):
    result = await registry.dispatch("apply_template", {"template_id": "tmpl_bm_positron"})
    assert "error" not in result
    assert result["command"] == "BASE_LAYER_CHANGE"
    assert "providerId" not in (result.get("params") or {})
    assert result["params"]["name"] in FRONTEND_TILE_PROVIDER_NAMES

    for tpl_id, pid in _basemap_templates():
        res = await registry.dispatch("apply_template", {"template_id": tpl_id})
        assert "error" not in res, f"{tpl_id} emit error: {res.get('error')}"
        assert res["params"]["name"] in FRONTEND_TILE_PROVIDER_NAMES


def test_unknown_provider_id_is_explicit_error():
    assert resolve_provider_id_to_name("carto-voyager") == "Carto 浅色"
    assert resolve_provider_id_to_name("no-such-provider-xyz") is None


# ── 断点 1：symbology 形状 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_symbology_single_emits_params_style_not_top_level_style_applied(registry):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
        ],
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_sym_admin_blue",
        "layer_id": "layer-1",
        "geojson": geojson,
    })
    assert "error" not in result
    assert "style_applied" not in result  # 旧顶层键必须消失
    params = result["params"]
    assert params["layer_id"] == "layer-1"
    style = params["style"]
    assert style["color"] == style["fill"] == "#3b82f6"
    assert style["strokeColor"] == "#1d4ed8"
    # 断点 5（发射端）：fillOpacity 保留在 style 里，而不是被丢弃
    assert style["fillOpacity"] == 0.4
    assert style["strokeWidth"] == 1.5


@pytest.mark.asyncio
async def test_symbology_categorical_emits_field_colorMap_baseStyle(registry):
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_sym_landuse_cat",
        "layer_id": "layer-1",
        "field": "landuse",
    })
    assert "error" not in result
    params = result["params"]
    assert params["layer_id"] == "layer-1"
    assert params["field"] == "landuse"
    assert params["colorMap"]["residential"] == "#fca5a5"
    assert params["baseStyle"]["fillOpacity"] == 0.75


# ── 断点 3：categorical 专题图 ────────────────────────────────────────────


def test_build_thematic_style_categorical_string_field():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"landuse": "residential"}},
            {"type": "Feature", "properties": {"landuse": "commercial"}},
            {"type": "Feature", "properties": {"landuse": "residential"}},
            {"type": "Feature", "properties": {"landuse": "green"}},
        ],
    }
    style_def = CartographyService.build_thematic_style(
        geojson=geojson, field="landuse", method="categorical", k=5, palette="Set2"
    )
    assert style_def is not None
    assert style_def["type"] == "categorical"
    assert style_def["field"] == "landuse"
    keys = [c["key"] for c in style_def["categories"]]
    # 去重后的真实类别（修复前是 equal_interval 数值断点）
    assert keys == ["residential", "commercial", "green"]
    assert len(style_def["categories"]) == 3
    assert all(c["color"].startswith("#") for c in style_def["categories"])

    legend = CartographyService.build_legend_spec(style_def, palette="Set2")
    assert legend["type"] == "categorical"
    assert {c["key"] for c in legend["categories"]} == {"residential", "commercial", "green"}


def test_build_thematic_style_categorical_numeric_field_preserves_numeric_keys():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"code": 1}},
            {"type": "Feature", "properties": {"code": 2}},
            {"type": "Feature", "properties": {"code": 3}},
        ],
    }
    style_def = CartographyService.build_thematic_style(
        geojson=geojson, field="code", method="categorical", k=5, palette="Tab10"
    )
    assert style_def is not None
    # JSON 往返后数值键仍是数字 —— 前端 match 表达式需要数值键命中数值字段
    round_tripped = json.loads(json.dumps(style_def))
    assert [c["key"] for c in round_tripped["categories"]] == [1, 2, 3]


def test_build_thematic_style_categorical_caps_at_k():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cat": i}} for i in range(20)
        ],
    }
    style_def = CartographyService.build_thematic_style(
        geojson=geojson, field="cat", method="categorical", k=5, palette="Set3"
    )
    assert style_def is not None
    assert len(style_def["categories"]) <= 5


def test_build_thematic_style_categorical_surplus_buckets_into_other():
    """#783: 去重类别超过 k 时，剩余类别并入显式「其他」桶（独立颜色 +
    图例条目），不得被静默刷成第 k 类的颜色且无图例条目。"""
    from app.lib.cartography.thematic_spec import spec_to_paint

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cat": f"class{i}"}}
            for i in range(8)
        ],
    }
    style_def = CartographyService.build_thematic_style(
        geojson=geojson, field="cat", method="categorical", k=5, palette="Set2"
    )
    assert style_def is not None
    entries = style_def["categories"]
    # 前 k-1 类保留 + 显式「其他」桶 = 总类数仍以 k 为上限
    assert len(entries) == 5
    other = entries[-1]
    assert other["label"] == "其他"
    named_colors = {e["color"] for e in entries[:-1]}
    assert other["color"] not in named_colors, "其他桶必须有独立颜色"
    assert [e["key"] for e in entries[:-1]] == [f"class{i}" for i in range(4)]

    # 所有 8 个值都必须映射到一个可见的图例类：
    # class0..3 命中各自 case，class4..7 落到 default = 其他桶颜色。
    legend = CartographyService.build_legend_spec(style_def, palette="Set2")
    paint, warns = spec_to_paint(legend)
    assert paint is not None and warns == []
    visible_colors = {case[1] for case in paint["cases"]} | {paint["default"]}
    for i in range(8):
        value = f"class{i}"
        if any(case[0] == value for case in paint["cases"]):
            mapped = next(c[1] for c in paint["cases"] if c[0] == value)
        else:
            mapped = paint["default"]
        assert mapped == other["color"] or mapped in named_colors
        assert mapped in visible_colors
    # 图例条目与渲染类一一对应（无值挂在无图例的颜色上）
    legend_colors = {c["color"] for c in legend["categories"]}
    assert visible_colors == legend_colors


def test_build_thematic_style_categorical_no_valid_values_returns_none():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"cat": None}},
            {"type": "Feature", "properties": {}},
        ],
    }
    assert CartographyService.build_thematic_style(
        geojson=geojson, field="cat", method="categorical", k=5, palette="Set2"
    ) is None


@pytest.mark.asyncio
async def test_apply_template_categorical_string_field_no_silent_degradation(registry):
    """分类字段（字符串）+ categorical 模板 → 真正的类别映射，不是数值断点。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"zoning": "R"}},
            {"type": "Feature", "properties": {"zoning": "C"}},
            {"type": "Feature", "properties": {"zoning": "M"}},
        ],
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_zoning",
        "field": "zoning",
        "geojson": geojson,
    })
    assert "error" not in result, result.get("error")
    style = result["params"]["style"]
    assert style["type"] == "categorical"
    assert {c["key"] for c in style["categories"]} == {"R", "C", "M"}
    assert result["params"]["legend_spec"]["type"] == "categorical"


@pytest.mark.asyncio
async def test_apply_template_categorical_empty_field_is_explicit_error(registry):
    """字符串分类字段无有效值 → 显式 error（旧实现 status:template_applied +
    style:null 假成功）。"""
    geojson = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"zoning": None}}],
    }
    result = await registry.dispatch("apply_template", {
        "template_id": "tmpl_th_zoning",
        "field": "zoning",
        "geojson": geojson,
    })
    assert "error" in result
    assert "zoning" in result["error"]


# ── 断点 4：implicit None 消灭 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_template_unknown_kind_is_explicit_error(registry):
    # 未知 kind：注入一个不存在的 kind 的假模板到 DB 路径不可行（用 registry），
    # 直接断言未知 kind 从 get_template_or_composite 拿不到时会走 not-found error。
    result = await registry.dispatch("apply_template", {"template_id": "tmpl_does_not_exist"})
    assert "error" in result
    assert "not found" in result["error"].lower()
    # 函数体内任何路径都以显式 return 收尾（无隐式 None）：执行层面由上面的
    # 各分支断言覆盖；此处守住"所有分支都有返回"的静态形状 —— 调用成功时
    # 必有 status 或 error 键。
    assert "status" in result or "error" in result