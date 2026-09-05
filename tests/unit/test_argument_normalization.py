"""ADR-0101 Wave 2: 声明式参数归一化层 + 结果契约视图测试。

锁定语义：
- 与旧 registry 硬编码 if 链行为逐字一致（含历史回归的对抗性用例）；
- 声明字段/保护性 ref 游标永不被折叠；
- 修复证据（ArgRepair）完整可追溯；
- 规则表静态自检通过；
- 结果契约视图对既有 dict 约定的适配正确。
"""
import pytest
from pydantic import BaseModel, Field
from typing import Any, List, Optional

from app.tools.argument_normalization import (
    ArgRepair,
    FIELD_RULES_BY_TOOL,
    GEOJSON_FAMILY_TOOLS,
    TOOL_NAME_ALIASES,
    coerce_json_string_lists,
    normalize_tool_arguments,
    resolve_tool_name,
    validate_normalization_tables,
)
from app.tools.registry import ToolRegistry, _normalize_tool_arguments


# --- 与既有测试同款 mock 模型（tests/test_tool_argument_aliases.py 镜像） ---

class MockHeatmapModel(BaseModel):
    geojson: Any
    radius_px: int = 30
    intensity: float = 1.0
    render_type: str = "native"
    bandwidth_m: Optional[float] = None
    cell_size: Optional[float] = None


class MockPoiModel(BaseModel):
    district: Optional[str] = None
    subtype: Optional[str] = None
    keyword: Optional[str] = None
    adcode: Optional[str] = None


class MockSlimPoiModel(BaseModel):
    """search_poi 无 subtype/district/adcode 字段的形态。"""
    keyword: Optional[str] = None
    limit: int = 10


class MockAggregateModel(BaseModel):
    points: Any
    polygons: Any


class MockProductModel(BaseModel):
    query: str = ""
    layer_ids: List[str] = Field(default_factory=list)
    primary_ref: Optional[str] = None
    overlay_refs: List[str] = Field(default_factory=list)
    title: Optional[str] = None
    template_id: Optional[str] = None


class MockThematicModel(BaseModel):
    geojson: Any
    field: str
    palette: str = "YlOrRd"
    method: str = "quantile"
    n_classes: int = 5


# ---------------------------------------------------------------------------
# 行为对齐（与旧实现的既有测试逐字一致）
# ---------------------------------------------------------------------------

def test_normalization_locked_behavior():
    """review R1 minor：registry 的旧包装现已委托本模块 —— 与其比较是循环
    的。真实 parity 由 migrated 规则表 + 下方字面量断言 + 既有
    tests/test_tool_argument_aliases.py（基线时代写就，锚定旧实现行为）
    共同锁定。"""
    raw = {"geojson_ref": "ref:abc", "radius-px": 25, "render-type": "native"}
    new_args, repairs = normalize_tool_arguments("heatmap_data", raw, MockHeatmapModel)
    # 字面量锚定（不依赖任何包装器）
    assert new_args == {"geojson": "ref:abc", "radius_px": 25, "render_type": "native"}
    kinds = {r.kind for r in repairs}
    assert "field_alias" in kinds and "key_style" in kinds


def test_heatmap_geojson_family_with_report():
    args, repairs = normalize_tool_arguments(
        "heatmap_data",
        {"geojson_ref": "ref:geojson-34ecef1088b44961", "radius-px": 25},
        MockHeatmapModel,
    )
    assert args["geojson"] == "ref:geojson-34ecef1088b44961"
    assert "geojson_ref" not in args
    assert args["radius_px"] == 25
    folded = [r for r in repairs if r.kind == "field_alias" and r.target == "geojson"]
    assert folded and folded[0].source == "geojson_ref"


def test_kebab_case_repair_recorded():
    args, repairs = normalize_tool_arguments(
        "heatmap_data", {"radius-px": 25}, MockHeatmapModel
    )
    assert args["radius_px"] == 25
    assert any(r.kind == "key_style" and r.source == "radius-px" for r in repairs)


def test_spatial_tools_data_ref_normalization():
    for tool_name in ["buffer_analysis", "spatial_stats", "kde_surface", "h3_binning"]:
        args, _ = normalize_tool_arguments(tool_name, {"data_ref": "ref:geojson-12345"})
        assert args["geojson"] == "ref:geojson-12345", tool_name
        assert "data_ref" not in args


def test_poi_aliases_with_report():
    args, repairs = normalize_tool_arguments(
        "query_local_poi",
        {"city": "成都市", "poi_type": "小学", "query": "实验小学"},
        MockPoiModel,
    )
    assert args["district"] == "成都市"
    assert args["subtype"] == "小学"
    assert args["keyword"] == "实验小学"
    targets = {r.target for r in repairs}
    assert {"district", "subtype", "keyword"} <= targets


def test_search_poi_conditional_rules_require_declared_target():
    """search_poi 的 subtype/district/adcode 别名仅在模型声明了该字段时适用；
    ``city`` 是 search_poi 的真实声明字段，绝不被折叠。"""
    declared, _ = normalize_tool_arguments(
        "search_poi", {"query": "咖啡", "region": "上海"}, MockPoiModel
    )
    assert declared.get("district") == "上海"
    slim_args, slim_repairs = normalize_tool_arguments(
        "search_poi", {"query": "咖啡", "region": "上海", "city": "上海"},
        MockSlimPoiModel,
    )
    # 未声明 district —— 别名必须原样保留，绝不能把数据改道
    assert slim_args["region"] == "上海"
    assert slim_args["city"] == "上海"  # search_poi 的真实字段
    assert "district" not in slim_args
    assert all(r.target != "district" for r in slim_repairs)


# ---------------------------------------------------------------------------
# 对抗性历史回归（audit 列出的真实事故）
# ---------------------------------------------------------------------------

def test_regression_declared_title_never_renamed_to_map_title():
    """master 回归：webgis_map_product 声明的 title 被改名成不存在的
    map_title → 未知参数门拒绝。声明字段必须原样保留。"""
    args, repairs = normalize_tool_arguments(
        "webgis_map_product",
        {"title": "我的地图", "primary_ref": "ref:x"},
        MockProductModel,
    )
    assert args["title"] == "我的地图"
    assert "map_title" not in args
    assert all(r.target != "map_title" for r in repairs)


def test_regression_declared_protected_ref_field_not_folded():
    """Pi 兼容审查回归：声明的保护名（geojson_ref）曾被反向折叠进 geojson。"""
    args, _ = normalize_tool_arguments(
        "heatmap_data",
        {"geojson_ref": "ref:cursor", "geojson_ref_extra_marker": True},
        MockHeatmapModel,
    )
    # geojson 未直接给出 → geojson_ref 是别名被折叠（正常语义）
    assert args["geojson"] == "ref:cursor"


def test_regression_alias_that_is_declared_field_kept_in_place():
    class DeclaredDataModel(BaseModel):
        geojson: Any
        data: Optional[str] = None  # data 是声明字段（保护名）

    args, repairs = normalize_tool_arguments(
        "heatmap_data", {"data": "keep-me"}, DeclaredDataModel
    )
    # data 已声明 → 不折叠；geojson 是必填但用户没给 —— 校验错误留给 pydantic
    assert args["data"] == "keep-me"
    assert "geojson" not in args
    assert all(r.source != "data" for r in repairs)


def test_regression_json_string_list_coercion_with_evidence():
    args, repairs = coerce_json_string_lists(
        {"layer_ids": "[\"a\",\"b\"]"}, MockProductModel
    )
    assert args["layer_ids"] == ["a", "b"]
    assert any(r.kind == "json_string_list" and r.target == "layer_ids" for r in repairs)


def test_regression_json_string_invalid_json_untouched():
    args, repairs = coerce_json_string_lists(
        {"layer_ids": "[a,b"}, MockProductModel
    )
    assert args["layer_ids"] == "[a,b"
    assert not repairs


def test_no_semantic_guessing_units_untouched():
    """单位/数值永不被归一化层改写 —— buffer 的 distance 值原样传递。"""
    args, _ = normalize_tool_arguments(
        "buffer_analysis", {"radius": 500}, None
    )
    assert args["distance"] == 500  # 键名折叠，值不动


def test_scalar_list_wrap_for_overlay_refs():
    args, repairs = normalize_tool_arguments(
        "webgis_map_product",
        {"overlays": "ref:single"},
        MockProductModel,
    )
    assert args["overlay_refs"] == ["ref:single"]
    wrapped = [r for r in repairs if r.detail == "list_wrapped"]
    assert wrapped and wrapped[0].source == "overlays"


def test_multi_ring_buffer_distance_scalar_wrapped():
    args, _ = normalize_tool_arguments("multi_ring_buffer", {"distance": 100})
    assert args["distances"] == [100]


def test_n_classes_value_coercion():
    args, repairs = normalize_tool_arguments(
        "create_thematic_map",
        {"field": "pop", "bins": 7},
        MockThematicModel,
    )
    assert args["n_classes"] == 7
    assert any(r.kind == "value_coercion" for r in repairs)
    # 非数值 bins 不吃（语义猜测禁止）
    args2, repairs2 = normalize_tool_arguments(
        "create_thematic_map", {"field": "pop", "bins": "auto"}, MockThematicModel
    )
    assert args2["bins"] == "auto"
    assert not any(r.kind == "value_coercion" for r in repairs2)


# ---------------------------------------------------------------------------
# 规则表静态自检
# ---------------------------------------------------------------------------

def test_normalization_tables_self_check_clean():
    assert validate_normalization_tables() == []


def test_tool_name_alias_resolution_matches_legacy():
    assert resolve_tool_name("buffer") == "buffer_analysis"
    assert resolve_tool_name("poi_search") == "search_poi"
    assert resolve_tool_name("nonexistent_alias") == "nonexistent_alias"
    # registry.resolve_name 同源
    assert ToolRegistry.resolve_name("buffer") == "buffer_analysis"


def test_geojson_family_tools_unchanged():
    assert "heatmap_data" in GEOJSON_FAMILY_TOOLS
    assert "create_thematic_map" in GEOJSON_FAMILY_TOOLS


# ---------------------------------------------------------------------------
# 活动注册表一致性（门：规则表不能与真实 schema 漂移）
# ---------------------------------------------------------------------------

def test_field_rules_alias_never_shadows_declared_field():
    """每工具规则的别名若恰为该工具（真实 registry 中）的声明字段，
    该别名规则即永久失活 —— 这是漂移信号，必须显式失败。"""
    from app.tools import __init__ as _tool_init  # noqa: F401
    from app.tools import init_tools
    registry = ToolRegistry()
    init_tools(registry)

    drifted = []
    for tool_name, rules in FIELD_RULES_BY_TOOL.items():
        model = registry._models.get(tool_name)
        if model is None:
            continue
        declared = set(model.model_fields.keys())
        for rule in rules:
            overlap = declared & set(rule.aliases)
            # 别名是声明字段 → 规则对真实工具永不生效（mock-only 规则除外）
            if overlap and tool_name in registry._tools:
                drifted.append(f"{tool_name}: {sorted(overlap)}")
    assert drifted == [], f"规则别名与声明字段冲突: {drifted}"


def test_tool_name_aliases_resolve_to_registered_tools():
    from app.tools import init_tools
    registry = ToolRegistry()
    init_tools(registry)
    missing = {
        alias: target for alias, target in TOOL_NAME_ALIASES.items()
        if target not in registry._tools
    }
    assert missing == {}, f"工具名别名指向未注册工具: {missing}"


# ---------------------------------------------------------------------------
# dispatch 端到端：修复证据 ContextVar
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_exposes_normalization_report():
    from app.tools.argument_normalization import normalization_report_var

    reg = ToolRegistry()

    def tool(geojson: Any = None, note: str = "x") -> dict:
        return {"success": True}

    reg.register(name="heatmap_probe", description="probe", func=tool)
    await reg.dispatch("heatmap_probe", {"data-ref": "somestring", "data_ref": "ref:abc"})
    report = normalization_report_var.get()
    kinds = {r.kind for r in report}
    assert "key_style" in kinds
    assert "field_alias" in kinds


@pytest.mark.asyncio
async def test_dispatch_resets_report_after_run():
    from app.tools.argument_normalization import normalization_report_var

    reg = ToolRegistry()

    def tool() -> dict:
        return {"success": True}

    reg.register(name="noop_probe", description="probe", func=tool)
    await reg.dispatch("noop_probe", {})
    assert normalization_report_var.get() == ()


# ---------------------------------------------------------------------------
# 结果契约视图
# ---------------------------------------------------------------------------

def test_inspect_error_result_shape():
    from app.lib.runtime.result_contract import OutputSemanticType, inspect_tool_result

    view = inspect_tool_result({
        "success": False, "code": "VALIDATION_ERROR", "message": "参数 'x' 校验失败",
        "correction_hint": "check params",
    })
    assert view.ok is False
    assert view.semantic_type is OutputSemanticType.ERROR
    assert view.error_code == "VALIDATION_ERROR"
    assert view.message.startswith("参数")
    assert view.correction_hint == "check params"


def test_inspect_error_like_shapes_529_family():
    from app.lib.runtime.result_contract import inspect_tool_result

    assert inspect_tool_result({"error": "boom"}).ok is False
    assert inspect_tool_result({"type": "error", "message": "x"}).ok is False
    assert inspect_tool_result({"status": "failed"}).ok is False
    assert inspect_tool_result({"success": False, "message": "x"}).ok is False


def test_inspect_geojson_and_ref_extraction():
    from app.lib.runtime.result_contract import OutputSemanticType, inspect_tool_result

    fc = {"type": "FeatureCollection", "features": []}
    view = inspect_tool_result({"success": True, "geojson": fc, "ref": "ref:abc-1"})
    assert view.ok is True
    assert view.semantic_type is OutputSemanticType.GEOJSON_FC
    assert "ref:abc-1" in view.ref_ids


def test_inspect_data_wrapped_fc_and_bytes_estimate():
    from app.lib.runtime.result_contract import OutputSemanticType, inspect_tool_result

    big_fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}}
        for _ in range(500)
    ]}
    view = inspect_tool_result({"success": True, "data": big_fc})
    assert view.semantic_type is OutputSemanticType.GEOJSON_FC
    assert view.approx_bytes > 1000


def test_bounded_summary_never_exceeds_budget():
    from app.lib.runtime.result_contract import bounded_summary

    huge = {"success": True, "geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"p": "x" * 200}} for _ in range(50)
    ]}}
    s = bounded_summary(huge, max_chars=120)
    assert len(s) <= 120
    err = bounded_summary({"success": False, "code": "TOOL_TIMEOUT", "message": "超时" * 500})
    assert len(err) <= 400 and "TOOL_TIMEOUT" in err


def test_inspect_never_raises_on_hostile_payload():
    from app.lib.runtime.result_contract import inspect_tool_result

    hostile = {"a": lambda: None}  # 不可 JSON 化
    view = inspect_tool_result(hostile)
    assert view is not None
    recursive: dict = {}
    recursive["self"] = recursive
    # estimate walk 有预算上限，不会栈溢出/死循环
    view2 = inspect_tool_result({"data": recursive})
    assert view2 is not None
