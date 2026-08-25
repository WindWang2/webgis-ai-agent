"""audit4 工具系统元数据修复回归（#990 / #995 / #996 / #1004）。

- #990: heatmap_data native 分支不得就地变更 get_shared 返回的共享 ref payload
        （>256KB 载荷走 pydantic 旁路直通，元数据只写进顶层浅拷贝）
- #995: 分类参数 schema 枚举 / 坐标 ge-le 范围 / 日期 pattern —— schema 层
        前置拦截，校验失败仍走 correction_hint 自愈通道
- #996: registry @tool cost 元数据通道（默认 light 全库兼容）+ 内部投递
        Celery 的 3 工具显式 heavy/timeout + webgis_map_intent /
        webgis_map_product contract_version bump（guidance 键，指纹 1.0#cv2）
- #1004: 参数级声明式 ref 游标通道 —— args model 字段
        json_schema_extra={"ref_cursor": True} 即跳过解引用（旧硬编码名单保留）
"""
import copy
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry


# ─── 共用 fixture ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def full_registry():
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _props_of(reg: ToolRegistry, name: str) -> dict:
    for schema in reg.get_schemas():
        fn = schema.get("function", {})
        if fn.get("name") == name:
            return fn.get("parameters", {}).get("properties", {})
    raise AssertionError(f"tool {name!r} not registered")


# ─── #990: 共享 ref payload 只读契约 ───────────────────────────────────


def _big_point_fc(n: int = 3000) -> dict:
    """>256KB 的点要素 FC —— 触发 registry 的 pydantic 大载荷旁路（dict 直通，
    无 model_dump 深拷贝保护），工具体拿到的就是 get_shared 返回的本体。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"i": i, "pad": "x" * 128},
                "geometry": {
                    "type": "Point",
                    "coordinates": [104.0 + i * 0.001, 30.6 + i * 0.001],
                },
            }
            for i in range(n)
        ],
    }


async def test_heatmap_native_does_not_pollute_shared_ref_payload():
    from app.services.session_data import session_data_manager
    from app.tools.spatial import register_spatial_tools

    reg = ToolRegistry()
    register_spatial_tools(reg)

    fc = _big_point_fc()
    snapshot = copy.deepcopy(fc)
    sid = "audit4-990-shared-ref"
    ref = await session_data_manager.store(sid, fc)
    assert isinstance(ref, str) and ref.startswith("ref:")

    r1 = await reg.dispatch(
        "heatmap_data",
        {"geojson": ref, "render_type": "native", "palette": "classic"},
        session_id=sid,
    )
    r2 = await reg.dispatch(
        "heatmap_data",
        {"geojson": ref, "render_type": "native", "palette": "magma"},
        session_id=sid,
    )

    # 工具输出自身仍携带 native 热力元数据（两次 palette 互不串扰）
    for r in (r1, r2):
        assert r.get("command") == "add_native_heatmap"
        assert r.get("type_hint") == "heatmap"
    assert r1["metadata"]["palette"] == "classic"
    assert r2["metadata"]["palette"] == "magma"

    # 会话存储中的原 ref payload 零污染：本体与快照逐字节一致
    stored = await session_data_manager.get_shared(sid, ref)
    assert stored is not None
    for key in ("command", "type_hint", "metadata", "legend_spec"):
        assert key not in stored, f"#990 regression: {key} leaked into stored ref payload"
    assert stored == snapshot


# ─── #995: schema 枚举 / 范围 / pattern 前置拦截 ────────────────────────


def test_spatial_heatmap_categorical_enums():
    from app.tools.spatial import register_spatial_tools

    reg = ToolRegistry()
    register_spatial_tools(reg)
    props = _props_of(reg, "heatmap_data")
    assert props["render_type"]["enum"] == ["native", "raster", "grid"]
    assert props["palette"]["enum"] == ["classic", "magma", "viridis", "thermal"]


def test_chinese_maps_plan_route_categorical_enums():
    from app.tools.chinese_maps import register_chinese_map_tools

    reg = ToolRegistry()
    register_chinese_map_tools(reg)
    props = _props_of(reg, "plan_route")
    assert props["mode"]["enum"] == ["driving", "walking", "cycling", "transit"]
    assert props["provider"]["enum"] == ["amap", "baidu"]


def test_network_tools_profile_and_objective_enums():
    from app.tools.network_tools import register_network_tools

    reg = ToolRegistry()
    register_network_tools(reg)
    props = _props_of(reg, "network_shortest_path")
    assert props["profile"]["enum"] == ["walking", "driving", "cycling", "custom"]
    # 同名分类参数在全部 network args model 上一致收敛（无遗漏的自由 str）
    for tool in ("network_od_matrix", "network_closest_facility",
                 "network_service_area", "network_accessibility",
                 "location_allocation"):
        assert _props_of(reg, tool)["profile"]["enum"] == [
            "walking", "driving", "cycling", "custom"]
    assert _props_of(reg, "location_allocation")["objective"]["enum"] == [
        "minimize_cost", "maximize_coverage"]


def test_change_detection_index_type_enum():
    from app.tools.change_detection import register_change_detection_tools

    reg = ToolRegistry()
    register_change_detection_tools(reg)
    props = _props_of(reg, "detect_vegetation_change")
    assert props["index_type"]["enum"] == ["ndvi", "ndwi", "nbr", "evi"]


def test_geocoding_reverse_lat_lon_ranges():
    from app.tools.geocoding import register_geocoding_tools

    reg = ToolRegistry()
    register_geocoding_tools(reg)
    props = _props_of(reg, "reverse_geocode")
    assert props["lat"]["minimum"] == -90
    assert props["lat"]["maximum"] == 90
    assert props["lon"]["minimum"] == -180
    assert props["lon"]["maximum"] == 180


def test_remote_sensing_date_pattern():
    from app.tools.remote_sensing import register_rs_tools

    reg = ToolRegistry()
    register_rs_tools(reg)
    for tool in ("fetch_sentinel", "compute_ndvi"):
        props = _props_of(reg, tool)
        for field in ("date_from", "date_to"):
            assert props[field].get("pattern") == r"^\d{4}-\d{2}-\d{2}$", (tool, field)


async def test_invalid_enum_rejected_with_self_heal_hint():
    """schema 拦截的参数错误仍是 VALIDATION_ERROR + correction_hint（LLM 自愈通道），
    且错误信息列出合法值 —— 不需要工具体执行即可纠正。"""
    from app.tools.spatial import register_spatial_tools

    reg = ToolRegistry()
    register_spatial_tools(reg)
    res = await reg.dispatch(
        "heatmap_data",
        {"geojson": _big_point_fc(12), "render_type": "native", "palette": "neon"},
        session_id="",
    )
    assert res.get("success") is False
    assert res.get("code") == "VALIDATION_ERROR"
    assert res.get("error_type") == "ValidationError"
    assert res.get("correction_hint")
    blob = str(res.get("message") or "") + res.get("correction_hint", "")
    assert "palette" in blob
    assert "classic" in blob  # 合法值可见，LLM 可自愈


async def test_out_of_range_coords_rejected_before_execution():
    from app.tools.geocoding import register_geocoding_tools

    reg = ToolRegistry()
    register_geocoding_tools(reg)
    res = await reg.dispatch("reverse_geocode", {"lat": 123.4, "lon": 116.4}, session_id="")
    assert res.get("success") is False
    assert res.get("code") == "VALIDATION_ERROR"
    assert res.get("correction_hint")


async def test_bad_date_format_rejected_before_execution():
    from app.tools.remote_sensing import register_rs_tools

    reg = ToolRegistry()
    register_rs_tools(reg)
    res = await reg.dispatch(
        "fetch_sentinel",
        {"bbox": "116.2,39.7,116.6,40.1", "date_from": "2026/07/01", "date_to": "2026-08-01"},
        session_id="",
    )
    assert res.get("success") is False
    assert res.get("code") == "VALIDATION_ERROR"
    assert res.get("correction_hint")


# ─── #996: cost 元数据 + heavy 标注 + contract bump ────────────────────


def test_cost_defaults_to_light_and_validates():
    reg = ToolRegistry()

    @reg.tool(name="cost_default_probe", description="probe")
    def _probe_a(x: int = 1) -> dict:
        return {}

    assert reg.metadata("cost_default_probe")["cost"] == "light"

    @reg.tool(name="cost_heavy_probe", description="probe", cost="heavy")
    def _probe_b(x: int = 1) -> dict:
        return {}

    assert reg.metadata("cost_heavy_probe")["cost"] == "heavy"

    with pytest.raises(ValueError, match="cost"):
        @reg.tool(name="cost_bad_probe", description="probe", cost="ultra")
        def _probe_c(x: int = 1) -> dict:
            return {}


def test_all_registered_tools_carry_valid_cost(full_registry):
    """守护：全库 cost ∈ {light, medium, heavy}（默认 light 全库兼容）。"""
    tools = full_registry.list_tools()
    assert len(tools) > 100  # live registry, not a stub
    for name in tools:
        cost = full_registry.metadata(name).get("cost")
        assert cost in ("light", "medium", "heavy"), f"{name}: cost={cost!r}"


def test_internal_celery_tools_marked_heavy(full_registry):
    """内部私自 apply_async / submit_durable_job 投递 Celery 的 3 个工具
    显式标 heavy 并声明独立墙钟预算。"""
    expected = {
        "heatmap_data": 300.0,        # 体内 task.get(timeout=120) 同步等结果（预算与原默认等量）
        "detect_vegetation_change": 60.0,   # submit_durable_job（DB 写 + 入队）
        "analyze_vegetation_index": 60.0,   # submit_durable_job（DB 写 + 入队）
    }
    for name, timeout in expected.items():
        meta = full_registry.metadata(name)
        assert meta.get("cost") == "heavy", f"{name}: cost={meta.get('cost')!r}"
        assert meta.get("timeout") == timeout, f"{name}: timeout={meta.get('timeout')!r}"


def test_webgis_harness_tools_contract_bumped_cv2(full_registry):
    """audit4 #979 给两个 harness 工具的 result 加了 guidance 键 —— RESULT
    契约变更必须 bump contract_version（指纹 1.0#cv2，lineage 可区分）。"""
    assert full_registry.tool_version("webgis_map_intent") == "1.0#cv2"
    assert full_registry.tool_version("webgis_map_product") == "1.0#cv2"


# ─── #1004: 声明式 ref 游标通道 ────────────────────────────────────────


class _CursorProbeArgs(BaseModel):
    cursor_ref: str = Field(
        ..., json_schema_extra={"ref_cursor": True},
        description="声明式 ref 游标：registry 不解引用，原样传给工具",
    )
    inline_data: Any = Field(
        ..., description="普通 ref 参数：透明解引用为完整载荷",
    )


class _CallableExtraProbeArgs(BaseModel):
    # callable 形态的 json_schema_extra 无法静态检视 —— 文档化的不支持路径
    cursor_ref: str = Field(
        ..., json_schema_extra=lambda schema: schema.update({"ref_cursor": True}),
    )


def test_declared_ref_cursor_keys_extraction():
    assert ToolRegistry._declared_ref_cursor_keys(_CursorProbeArgs) == {"cursor_ref"}
    assert ToolRegistry._declared_ref_cursor_keys(_CallableExtraProbeArgs) == set()
    assert ToolRegistry._declared_ref_cursor_keys(None) == set()


async def test_ref_cursor_field_skips_dereference_normal_ref_resolves():
    """声明式游标字段不解引用（原样 ref 字符串），普通 ref 字段照常解引用。"""
    from app.services.session_data import session_data_manager

    reg = ToolRegistry()

    @reg.tool(name="cursor_probe", description="probe", args_model=_CursorProbeArgs)
    async def _cursor_probe(cursor_ref: str, inline_data: Any = None) -> dict:
        return {"cursor_ref": cursor_ref, "inline_data": inline_data}

    sid = "audit4-1004-ref-cursor"
    payload = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {
            "type": "Point", "coordinates": [104.0, 30.6]}}
    ]}
    ref = await session_data_manager.store(sid, payload)

    res = await reg.dispatch(
        "cursor_probe",
        {"cursor_ref": ref, "inline_data": ref},
        session_id=sid,
    )

    # 游标字段：原样 ref 字符串（未被解引用为 FeatureCollection）
    assert res["cursor_ref"] == ref
    assert isinstance(res["cursor_ref"], str)
    # 普通字段：透明解引用为完整载荷
    assert res["inline_data"] == payload


async def test_legacy_hardcoded_skip_keys_still_honored():
    """旧硬编码名单保留兼容：ref_id 字段仍不解引用（存量不迁移）。"""
    from app.services.session_data import session_data_manager

    class _LegacyProbeArgs(BaseModel):
        ref_id: Any = Field(None)
        inline_data: Any = Field(None)

    reg = ToolRegistry()

    @reg.tool(name="legacy_skip_probe", description="probe", args_model=_LegacyProbeArgs)
    async def _legacy_probe(ref_id: Any = None, inline_data: Any = None) -> dict:
        return {"ref_id": ref_id, "inline_data": inline_data}

    sid = "audit4-1004-legacy-skip"
    payload = {"type": "FeatureCollection", "features": []}
    ref = await session_data_manager.store(sid, payload)

    res = await reg.dispatch(
        "legacy_skip_probe",
        {"ref_id": ref, "inline_data": ref},
        session_id=sid,
    )
    assert res["ref_id"] == ref
    assert res["inline_data"] == payload
