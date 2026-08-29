"""ToolDispatchService 单测（统一工具调度）：

接口即测试面。通过 dispatch() 的返回值（判别式结果 dataclass）断言可观察结果，
不窥探内部状态。两条 agent 路径（legacy ChatEngine + Pi bridge）共用本服务。

harness 约定：clean_session fixture、fake_registry（MagicMock + AsyncMock）、
_tc 工具调用辅助。
"""
import json

import pytest
import shutil
from unittest.mock import AsyncMock, MagicMock

from app.services.tool_dispatch_service import (
    LEGACY_TOOL_NAME_MAP,
    ToolDispatchResult,
    ToolDispatchService,
    normalize_tool_name,
)
from app.services.session_data import session_data_manager
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance


@pytest.fixture
async def clean_session():
    sid = "test-dispatch-service-session"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


@pytest.fixture
def fake_registry():
    return MagicMock(dispatch=AsyncMock())


def _tc(name: str, args: dict, tc_id: str = "call_1") -> dict:
    return {"id": tc_id, "function": {"name": name, "arguments": args}}


@pytest.fixture
def service(fake_registry):
    # 新服务：依赖注入（registry + 可选 broadcast 回调），与旧 dispatcher 一致。
    return ToolDispatchService(registry=fake_registry)


# ─── 判别式结果：三个 status 分支 ──────────────────────────


@pytest.mark.asyncio
async def test_success_returns_ok_status(service, fake_registry, clean_session):
    """正常工具返回 → status == 'ok'，且携带 llm_payload / slim_event。"""
    fake_registry.dispatch.return_value = {"summary": "info", "stat": 42}
    result = await service.dispatch(_tc("spatial_stats", {}), clean_session, set())
    assert isinstance(result, ToolDispatchResult)
    assert result.status == "ok"
    assert result.llm_payload
    assert result.slim_event is not None
    assert result.error_msg is None


@pytest.mark.asyncio
async def test_repeated_call_intercepted_returns_repeated_status(service, fake_registry, clean_session):
    """同 session 内同名同参数二次调用 → status == 'repeated'，registry.dispatch 只调一次。"""
    executed: set = set()
    tc = _tc("geocode_cn", {"q": "北京"})
    fake_registry.dispatch.return_value = {"summary": "ok"}

    r1 = await service.dispatch(tc, clean_session, executed)
    assert r1.status == "ok"

    r2 = await service.dispatch(tc, clean_session, executed)
    assert r2.status == "repeated"
    assert "[重复调用拦截]" in r2.llm_payload
    assert fake_registry.dispatch.call_count == 1


@pytest.mark.asyncio
async def test_registry_exception_wrapped_as_error_status(service, fake_registry, clean_session):
    """registry.dispatch 抛 ValueError → status == 'error'，llm_payload 含自愈提示。"""
    fake_registry.dispatch.side_effect = ValueError("无法找到引用数据 ref:bogus 校验失败")
    result = await service.dispatch(_tc("x", {}), clean_session, set())
    assert result.status == "error"
    assert result.error_msg is not None
    assert "无法找到" in result.llm_payload or "参数校验" in result.llm_payload
    assert result.geojson_ref is None


@pytest.mark.asyncio
async def test_std_error_dict_wrapped_as_error_status(service, fake_registry, clean_session):
    """registry 返回 std_error_response dict → status == 'error'，写 tool_failed 事件。"""
    fake_registry.dispatch.return_value = {
        "success": False,
        "code": "NOT_FOUND",
        "message": "区域不存在",
        "error_type": "KeyError",
    }
    result = await service.dispatch(_tc("get_district", {"name": "ghost"}), clean_session, set())
    assert result.status == "error"
    assert "区域不存在" in result.error_msg
    log = await session_data_manager.get_event_log(clean_session)
    assert any(e["event"] == "tool_failed" for e in log)


# ─── 回归锁定：geojson_ref（本批工作修复的核心 bug） ────────────


@pytest.mark.asyncio
async def test_geojson_result_produces_ref(service, fake_registry, clean_session):
    """【回归锁定】工具返回 FeatureCollection → geojson_ref 非空。

    这是整个 unified-tool-dispatch 工作要修复的静默回归：Pi 路径当前从不产生 ref，
    导致前端图层挂载逻辑（键 off geojson_ref）找不到图层可挂。该用例确保新服务
    无论被哪条路径调用都会落 ref。
    """
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, "properties": {}}],
        "summary": "1 point",
    }
    result = await service.dispatch(_tc("search_poi", {"q": "school"}, tc_id="call_42"), clean_session, set())
    assert result.status == "ok"
    assert result.geojson_ref is not None
    assert result.geojson_ref.startswith("ref:geojson-")
    assert result.raw_result["mapspec_fingerprint"].startswith("carto-sha256:")
    assert result.raw_result["runtime_patch"]["result_ref"] == result.geojson_ref
    assert result.raw_result["mutation_revision"] == 1
    assert result.map_actions[0]["command"] == "add_layer"
    persisted = await mapspec_store_instance.get_mapspec(clean_session)
    assert persisted["layers"][0]["provenance"]["result_ref"] == result.geojson_ref


# ─── #517 回归锁定：to_llm_response() 的 data 包裹 FC 形状 ──────────


@pytest.mark.asyncio
async def test_data_wrapped_fc_produces_ref(service, fake_registry, clean_session):
    """#517：to_llm_response() 形状 {success, summary, data: FeatureCollection}
    必须走与顶层 FC 相同的挂载管线 → geojson_ref 非空且 mapspec 落库。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, "properties": {"v": 1}},
        ],
    }
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "1 point buffered",
        "data": fc,
        "bbox": "116.4,39.9,116.4,39.9",
    }
    result = await service.dispatch(_tc("buffer_analysis", {"radius": 100}, tc_id="call_517_1"), clean_session, set())
    assert result.status == "ok"
    assert result.geojson_ref is not None
    assert result.geojson_ref.startswith("ref:geojson-")
    # data 包裹的 FC 也要经过 MapSpec authoring（前端靠 runtime_patch/挂载）
    assert result.raw_result["runtime_patch"]["result_ref"] == result.geojson_ref
    assert result.map_actions[0]["command"] == "add_layer"


@pytest.mark.asyncio
async def test_data_wrapped_fc_geometry_persisted(service, fake_registry, clean_session):
    """#517 几何真值：data 包裹 FC 落 ref 后可从 session_data_manager 读回，
    要素数与几何类型与输入一致（不是"不抛异常"的假通过）。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 2]]]}, "properties": {}},
        ],
    }
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "2 polygons",
        "data": fc,
    }
    result = await service.dispatch(_tc("buffer_analysis", {"radius": 50}, tc_id="call_517_2"), clean_session, set())
    assert result.status == "ok" and result.geojson_ref

    res = await session_data_manager.get_ref_data(clean_session, result.geojson_ref)
    assert res.success and res.data
    persisted = res.data
    assert persisted.get("type") == "FeatureCollection"
    assert len(persisted["features"]) == 2
    assert {f["geometry"]["type"] for f in persisted["features"]} == {"Polygon"}


@pytest.mark.asyncio
async def test_llm_payload_carries_ref_id_for_data_wrapped_fc(service, fake_registry, clean_session):
    """#517：summary 分支的 LLM 载荷必须携带 ref_id == geojson_ref，
    LLM 才拿得到 ref 去 display_layer。"""
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
    }
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "done",
        "data": fc,
    }
    result = await service.dispatch(_tc("kde_surface", {"bandwidth": 5}, tc_id="call_517_3"), clean_session, set())
    assert result.status == "ok" and result.geojson_ref
    assert '"ref_id"' in result.llm_payload
    import json as _json
    payload = _json.loads(result.llm_payload)
    assert payload.get("ref_id") == result.geojson_ref


@pytest.mark.asyncio
async def test_slim_event_excludes_data_key(service, fake_registry, clean_session):
    """#517 性能面 sibling：slim_event（SSE 事件载荷）不得再携带未裁剪的
    data FC 全量传输。"""
    big_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, i]}, "properties": {"p": i}}
            for i in range(100)
        ],
    }
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "big",
        "data": big_fc,
    }
    result = await service.dispatch(_tc("spatial_stats", {}, tc_id="call_517_4"), clean_session, set())
    assert result.status == "ok"
    slim = result.slim_event
    assert isinstance(slim, dict)
    assert "data" not in slim
    assert "geojson" not in slim
    assert "features" not in slim


@pytest.mark.asyncio
async def test_data_wrapped_non_fc_produces_no_ref(service, fake_registry, clean_session):
    """#517 守卫：data 不是 FC dict（list / 普通 dict）时不得误挂载。"""
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "tabular",
        "data": [{"a": 1}, {"a": 2}],
    }
    result = await service.dispatch(_tc("some_table_tool", {}, tc_id="call_517_5"), clean_session, set())
    assert result.status == "ok"
    assert result.geojson_ref is None


@pytest.mark.asyncio
async def test_mapspec_authoring_failure_keeps_ref_but_drops_feature_body(
    service, fake_registry, clean_session, monkeypatch,
):
    """L1 analysis may survive presentation failure without retaining a
    duplicate dataset or fabricating a cartographic generation."""
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {"private": "not-evidence"},
        }],
    }
    fake_registry.dispatch.return_value = feature_collection
    from app.services.mapspec_store import mapspec_store
    monkeypatch.setattr(
        mapspec_store,
        "layer_upsert",
        AsyncMock(side_effect=RuntimeError("authoring unavailable")),
    )

    result = await service.dispatch(
        _tc("search_poi", {"q": "school"}, tc_id="call_failed_author"),
        clean_session,
        set(),
    )

    assert result.status == "ok"
    assert result.geojson_ref and result.geojson_ref.startswith("ref:geojson-")
    assert result.raw_result["result_ref"] == result.geojson_ref
    assert result.raw_result["feature_count"] == 1
    assert "features" not in result.raw_result
    assert "private" not in result.llm_payload
    assert result.raw_result["cartographic_review"]["status"] == "not_evaluated"
    assert "mapspec_fingerprint" not in result.raw_result
    assert result.map_actions == []


@pytest.mark.asyncio
async def test_no_geojson_means_no_ref(service, fake_registry, clean_session):
    """无几何的工具返回 → geojson_ref is None（非几何工具不应误造 ref）。"""
    fake_registry.dispatch.return_value = {"summary": "info", "stat": 42}
    result = await service.dispatch(_tc("spatial_stats", {}), clean_session, set())
    assert result.status == "ok"
    assert result.geojson_ref is None


@pytest.mark.asyncio
async def test_existing_result_ref_reuses_metadata_without_copying_data(
    service, fake_registry, clean_session,
):
    source = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
            "properties": {},
        }],
    }
    existing_ref = await session_data_manager.store(
        clean_session, source, prefix="geojson"
    )
    fake_registry.dispatch.return_value = {
        "summary": "authored",
        "result_ref": existing_ref,
        "command": "add_layer",
        "params": {"result_ref": existing_ref},
    }

    result = await service.dispatch(
        _tc("webgis_layer_upsert", {}), clean_session, set()
    )

    assert result.geojson_ref == existing_ref
    assert result.ref_descriptor is not None
    assert result.ref_descriptor["feature_count"] == 1
    refs = await session_data_manager.list_refs(clean_session)
    assert list(refs) == [existing_ref]


@pytest.mark.asyncio
async def test_result_ref_mount_identity_survives_descriptor_cache_miss(
    service, fake_registry, clean_session, monkeypatch,
):
    result_ref = "ref:geojson-transient-result"
    fake_registry.dispatch.return_value = {
        "summary": "authored",
        "result_ref": result_ref,
    }
    monkeypatch.setattr(
        session_data_manager,
        "get_ref_descriptor",
        AsyncMock(return_value=None),
    )

    result = await service.dispatch(
        _tc("webgis_layer_upsert", {}), clean_session, set()
    )

    assert result.status == "ok"
    assert result.geojson_ref == result_ref
    assert result.ref_descriptor is None


@pytest.mark.asyncio
async def test_raster_result_ref_never_masquerades_as_geojson(
    service, fake_registry, clean_session, monkeypatch,
):
    """Raster identity stays on the image path; no empty vector mount may ACK it."""
    fake_registry.dispatch.return_value = {
        "type": "heatmap_raster",
        "image": "/api/v1/sessions/s/raster/r1.png",
        "bbox": [116.0, 39.0, 117.0, 40.0],
        "result_ref": "ref:raster/r1",
        "command": "add_heatmap_raster",
    }
    descriptor = AsyncMock(side_effect=AssertionError("raster is not GeoJSON"))
    monkeypatch.setattr(session_data_manager, "get_ref_descriptor", descriptor)

    result = await service.dispatch(
        _tc("webgis_layer_upsert", {}), clean_session, set()
    )

    assert result.status == "ok"
    assert result.geojson_ref is None
    assert result.ref_descriptor is None
    assert result.raw_result["result_ref"] == "ref:raster/r1"
    descriptor.assert_not_awaited()


@pytest.mark.asyncio
async def test_heatmap_raster_enters_mapspec_review(service, fake_registry, clean_session):
    """Matt P1: a heatmap_data raster result is authored into MapSpec review
    without being advertised as a GeoJSON mount."""
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
        "AAAABJRU5ErkJggg=="
    )
    fake_registry.dispatch.return_value = {
        "type": "heatmap_raster",
        "image": f"data:image/png;base64,{png}",
        "bbox": [116.0, 39.0, 117.0, 40.0],
        "legend_spec": {"type": "continuous", "field": "density"},
        "command": "add_heatmap_raster",
    }

    result = await service.dispatch(
        _tc("heatmap_data", {"render_type": "raster"}, tc_id="heat_1"),
        clean_session,
        set(),
    )

    assert result.status == "ok"
    assert result.geojson_ref is None
    assert result.raw_result["type"] == "heatmap_raster"
    assert result.raw_result["result_ref"].startswith("ref:raster/")
    assert result.raw_result["mapspec_fingerprint"].startswith("carto-sha256:")
    assert result.raw_result["cartographic_review"]["status"] in {
        "passed", "passed_with_warnings", "failed_repairable",
        "failed_unrepairable", "repair_exhausted", "not_evaluated",
    }
    # #533: authoring 必须把可寻址 image URL 放回 raw_result 与命令 params。
    # 此前的实现把 producer 的 data-URL 剥掉（_DISPLAY_RESULT_METADATA_KEYS
    # 不含 image），命令 params 无 image → 前端 add_heatmap_raster 校验器拒绝
    # （invalid_params）且 auto-mount gate 不满足，图层永不挂载。
    commands = result.raw_result["commands"]
    assert len(commands) == 1
    assert commands[0]["command"] == "add_heatmap_raster"
    params = commands[0]["params"]
    image_url = result.raw_result["image"]
    assert image_url.startswith(f"/api/v1/sessions/{clean_session}/raster/")
    assert image_url.endswith("/raster/raster-heat_1-source.png")
    assert params["image"] == image_url
    assert params["bbox"] == [116.0, 39.0, 117.0, 40.0]
    # 命令必须被铸成 SSE map action（前端经此消费执行）。
    assert any(ma["command"] == "add_heatmap_raster" for ma in result.map_actions)
    persisted = await mapspec_store_instance.get_mapspec(clean_session)
    assert persisted["layers"][0]["type"] == "raster"
    assert persisted["layers"][0]["provenance"]["result_ref"] == result.raw_result["result_ref"]


@pytest.mark.asyncio
async def test_heatmap_raster_command_satisfies_frontend_contract(
    service, fake_registry, clean_session,
):
    """#533 契约（producer → authoring → 前端消费）：发射的命令参数必须满足
    前端 add_heatmap_raster 校验器与 run gate 的真实形状：
    - requiredParams: typeof p.image === 'string'（或 p.url）→ image 必须为 URL 串；
    - run gate: image 与 bbox 都必须 truthy；
    - bbox 为 4 元素数值列表（[w,s,e,n]，MapLibre coords 换算依赖）；
    - auto-mount gate: data.result.image truthy。
    （#535 的"命令名 ∈ catalogue"不变量抓不到 #533 —— 命令名存在、参数形状
    错误，需要本形状测试兜底。）
    """
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
        "AAAABJRU5ErkJggg=="
    )
    fake_registry.dispatch.return_value = {
        "type": "heatmap_raster",
        "image": f"data:image/png;base64,{png}",
        "bbox": [116.5, 39.5, 117.5, 40.5],
        "legend_spec": {"type": "continuous", "field": "density"},
        "command": "add_heatmap_raster",
    }

    result = await service.dispatch(
        _tc("heatmap_data", {"render_type": "raster"}, tc_id="heat_2"),
        clean_session,
        set(),
    )

    assert result.status == "ok"
    command = result.raw_result["commands"][0]
    assert command["command"] == "add_heatmap_raster"
    params = command["params"]
    # 前端校验器 predicate（heatmapCommands.ts requiredParams 镜像）
    assert isinstance(params.get("image"), str), "params.image 必须是字符串（URL）"
    assert params.get("image"), "params.image 必须 truthy（run gate）"
    assert params.get("bbox"), "params.bbox 必须 truthy（run gate）"
    bbox = params["bbox"]
    assert isinstance(bbox, list) and len(bbox) == 4
    assert all(isinstance(v, (int, float)) and v == float(v) for v in bbox)
    assert bbox == [116.5, 39.5, 117.5, 40.5]
    # auto-mount gate（use-sse-stream: data.geojson_ref || data.result?.image）
    assert result.raw_result.get("image"), "raw_result.image 必须 truthy（auto-mount gate）"
    assert result.raw_result["image"] == params["image"]


@pytest.mark.asyncio
async def test_broadcast_fired_when_ref_produced(service, fake_registry, clean_session):
    """产生 ref 时触发 WS 广播；event_log 记录 tool_executed。"""
    broadcasts: list[tuple] = []
    svc = ToolDispatchService(
        registry=fake_registry,
        fire_broadcast=lambda *a: broadcasts.append(a),
    )
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
    }
    result = await svc.dispatch(_tc("search_poi", {}, tc_id="call_99"), clean_session, set())
    assert result.geojson_ref
    assert len(broadcasts) == 1
    assert broadcasts[0][1] == "geojson_update"
    assert broadcasts[0][2]["step_id"] == "call_99"
    assert broadcasts[0][2]["geojson"] == result.geojson_ref
    log = await session_data_manager.get_event_log(clean_session)
    assert any(e["event"] == "tool_executed" for e in log)


@pytest.mark.asyncio
async def test_no_broadcast_when_no_ref(service, fake_registry, clean_session):
    """无 ref 时不应广播。"""
    broadcasts: list[tuple] = []
    svc = ToolDispatchService(
        registry=fake_registry,
        fire_broadcast=lambda *a: broadcasts.append(a),
    )
    fake_registry.dispatch.return_value = {"summary": "info"}
    await svc.dispatch(_tc("x", {}), clean_session, set())
    assert broadcasts == []


@pytest.mark.asyncio
async def test_fire_broadcast_none_is_safe(service, fake_registry, clean_session):
    """fire_broadcast=None 时不应抛错（subagent / 纯测试场景）。"""
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
    }
    result = await service.dispatch(_tc("x", {}), clean_session, set())
    assert result.geojson_ref  # 仍会生成 ref


@pytest.mark.asyncio
async def test_suspicious_result_appends_hint(service, fake_registry, clean_session):
    """空 FeatureCollection → llm_payload 附自愈提示尾。"""
    fake_registry.dispatch.return_value = {"type": "FeatureCollection", "features": []}
    result = await service.dispatch(_tc("query_osm_poi", {"area": "..."}), clean_session, set())
    assert result.status == "ok"  # 空结果不是错误，是可疑
    assert "未返回任何空间要素" in result.llm_payload


@pytest.mark.asyncio
async def test_failed_dispatch_does_not_occupy_dedup_slot(service, fake_registry, clean_session):
    """R-dedup（design-v3 §2）：失败调用不占用 dedup 槽位——同参重试放行，
    且绝不会收到「已成功执行」的重复提示；成功后的同参重复仍被拦截。"""
    executed: set = set()
    tc = _tc("query_osm", {"area": "北京"})
    fake_registry.dispatch.side_effect = [
        {"success": False, "code": "VALIDATION_ERROR", "message": "参数校验失败"},
        {"summary": "ok"},
    ]
    r1 = await service.dispatch(tc, clean_session, executed)
    assert r1.status == "error"
    # 失败后同参重试 → 正常 dispatch（不是 repeated，不谎报成功）
    r2 = await service.dispatch(tc, clean_session, executed)
    assert r2.status == "ok"
    assert fake_registry.dispatch.call_count == 2
    # 成功后同参再调 → repeated（成功重复语义不变）
    r3 = await service.dispatch(tc, clean_session, executed)
    assert r3.status == "repeated"
    assert "[重复调用拦截]" in r3.llm_payload
    assert fake_registry.dispatch.call_count == 2


def test_tool_name_normalization_table():
    """断言 legacy 工具名被正确映射为 webgis_* canonical 名称。

    #516：remove_layer / zoom_to_layer 已是 registry 现役工具，别名表不得
    再改写它们（schema 不兼容：layer_ref vs layer_id / 全可选 view args），
    否则 LLM 的合法调用被重定向后校验失败或静默无操作。LLM 按目录可见名
    调用即命中现役工具。
    """
    assert normalize_tool_name("add_layer") == "webgis_layer_upsert"
    assert normalize_tool_name("set_layer_style") == "webgis_layer_upsert"
    assert normalize_tool_name("set_view") == "webgis_view_set"
    # #516：现役工具名不再被别名表改写
    assert normalize_tool_name("remove_layer") == "remove_layer"
    assert normalize_tool_name("zoom_to_layer") == "zoom_to_layer"
    assert normalize_tool_name("init_project") == "webgis_project_init"
    assert normalize_tool_name("unknown_tool") == "unknown_tool"


def test_legacy_alias_map_never_shadows_registered_tool_names():
    """契约：别名表键 ∩ registry 注册名 == ∅（防 #516 复发）。

    别名表只应包含不再注册的旧名；一旦某工具以现役名注册
    （remove_layer / zoom_to_layer），dispatch 入口的 normalize 会把
    LLM 对现役工具的合法调用重写为 schema 不兼容的 webgis_* 工具。
    """
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    registered = set(r.all_metadata().keys())
    conflict = set(LEGACY_TOOL_NAME_MAP.keys()) & registered
    assert conflict == set(), (
        f"LEGACY_TOOL_NAME_MAP 键与 registry 注册名冲突，会被 normalize "
        f"错误改写: {conflict}"
    )
    # #516 的两个现役工具必须在 registry 中且不在别名表中
    assert "remove_layer" in registered
    assert "zoom_to_layer" in registered
    assert "remove_layer" not in LEGACY_TOOL_NAME_MAP
    assert "zoom_to_layer" not in LEGACY_TOOL_NAME_MAP


@pytest.mark.asyncio
async def test_dispatch_does_not_rewrite_active_tool_names(
    service, fake_registry, clean_session,
):
    """#516：现役工具 remove_layer / zoom_to_layer 经 dispatch 不被改写，
    且参数 schema 按现役定义校验（layer_ref 必须原样到达 registry）。"""
    fake_registry.dispatch.return_value = {"summary": "removed"}
    tc = _tc("remove_layer", {"layer_ref": "ref:geojson-1"})

    result = await service.dispatch(tc, clean_session, set())

    assert result.status == "ok"
    fake_registry.dispatch.assert_called_once()
    called_name, called_args = fake_registry.dispatch.call_args[0][0], fake_registry.dispatch.call_args[0][1]
    assert called_name == "remove_layer"
    assert called_args == {"layer_ref": "ref:geojson-1"}


@pytest.mark.asyncio
async def test_dispatch_zoom_to_layer_passes_padding_through(
    service, fake_registry, clean_session,
):
    """#516：zoom_to_layer 的 layer_ref/padding 原样到达 registry（改写为
    webgis_view_set 会吞掉这两个参数，缩放命令永不触发）。"""
    fake_registry.dispatch.return_value = {"summary": "zoomed"}
    tc = _tc("zoom_to_layer", {"layer_ref": "ref:geojson-2", "padding": 120})

    result = await service.dispatch(tc, clean_session, set())

    assert result.status == "ok"
    fake_registry.dispatch.assert_called_once()
    called_name, called_args = fake_registry.dispatch.call_args[0][0], fake_registry.dispatch.call_args[0][1]
    assert called_name == "zoom_to_layer"
    assert called_args == {"layer_ref": "ref:geojson-2", "padding": 120}


@pytest.mark.asyncio
async def test_dispatch_normalizes_legacy_tool_names(service, fake_registry, clean_session):
    """【Seam B】通过 legacy 工具名 dispatch → registry.dispatch 被调用的工具名被规范化为 canonical webgis_*。"""
    fake_registry.dispatch.return_value = {"summary": "layer upserted"}
    tc = _tc("add_layer", {"layer_id": "test_layer"})
    
    result = await service.dispatch(tc, clean_session, set())
    
    assert result.status == "ok"
    fake_registry.dispatch.assert_called_once()
    called_tool_name = fake_registry.dispatch.call_args[0][0]
    assert called_tool_name == "webgis_layer_upsert"



# ─── P2-9（adversarial P2-9 / recovery P2）：并发在飞去重不谎报成功 ──


@pytest.mark.asyncio
async def test_concurrent_inflight_duplicate_does_not_claim_success(service, fake_registry, clean_session):
    """同一波次并发同参调用：原调用仍在执行中时，重复调用被拦截但**绝不声称
    "已成功执行"**——消息软化说明原调用已发起，让 LLM 以原调用结果为准。
    原调用完成后，post-success dedup 文案保持不变。"""
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_dispatch(name, args, session_id=None):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)
        return {"summary": "ok"}

    fake_registry.dispatch.side_effect = slow_dispatch
    executed: set = set()
    tc = _tc("geocode_cn", {"q": "北京"})

    first = asyncio.create_task(service.dispatch(tc, clean_session, executed))
    await asyncio.wait_for(entered.wait(), timeout=5.0)  # 原调用在飞

    dup = await service.dispatch(tc, clean_session, executed)  # 并发第二发
    assert dup.status == "repeated"
    assert "[重复调用拦截]" in dup.llm_payload
    # 关键：不得谎报成功（旧文案含"已成功执行"）
    assert "已成功执行" not in dup.llm_payload
    assert "仍在执行中" in dup.llm_payload

    release.set()
    r1 = await asyncio.wait_for(first, timeout=5.0)
    assert r1.status == "ok"

    # 原调用完成后，同参再调 → post-success 语义。audit4 #984 后的诚实契约：
    # 声明「未重新执行、结果是先前状态、上下文变了要微调参数」——
    # 不再使用旧的「已成功执行/直接汇报」成功口吻。
    r2 = await service.dispatch(tc, clean_session, executed)
    assert r2.status == "repeated"
    assert "以相同参数执行过" in r2.llm_payload
    assert "未重新执行" in r2.llm_payload
    assert "已成功执行" not in r2.llm_payload


# ── 原生热力图端到端授权：type_hint=heatmap → MapSpec 落 heatmap 图层 ──


@pytest.mark.asyncio
async def test_native_heatmap_result_authors_heatmap_mapspec_layer(
    service, fake_registry, clean_session
):
    """【回归锁定】heatmap_data native 的结果（FC + type_hint + metadata）经
    dispatch MapSpec 授权必须落 type=heatmap 图层 + 官方范式 paint。

    回归现场：type_hint 缺失时点要素被推断为 circle，FC 体被白名单剥离，
    add_native_heatmap 指令丢失 —— 热力图从未在地图上出现。
    #690: point_count 需 >= 阈值(10)否则被 MapSpec converter 守卫拦为 circle。
    """
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]}, "properties": {}}
            for i in range(12)
        ],
        "command": "add_native_heatmap",
        "type_hint": "heatmap",
        "metadata": {"render_type": "native", "point_count": 12,
                     "radius": 1500, "palette": "thermal"},
        "legend_spec": {"type": "continuous", "min": 0.0, "max": 1.0,
                        "palette": "YlOrRd", "palette_colors": ["#0066ff", "#eb1414"]},
    }
    result = await service.dispatch(
        _tc("heatmap_data", {"geojson": "...", "render_type": "native"}, tc_id="call_heat"),
        clean_session, set(),
    )
    assert result.status == "ok"
    assert result.map_actions[0]["command"] == "add_layer"

    persisted = await mapspec_store_instance.get_mapspec(clean_session)
    heat = next(layer for layer in persisted["layers"] if layer["type"] == "heatmap")
    paint = heat["paint"]
    for key in ("heatmap-weight", "heatmap-intensity", "heatmap-color",
                "heatmap-radius", "heatmap-opacity"):
        assert key in paint
    # 色带用 palette=thermal 的停靠点色（legacy 米制 radius 经 heatmap_contract
    # 归一化 → 视觉默认 30px，绝不按 1500px 消费）
    assert "#0066ff" in str(paint["heatmap-color"])
    assert paint["heatmap-radius"][6] == 30
    assert heat["heatmap"]["radius_px"] == 30
    assert heat["heatmap"]["bandwidth_m"] == 1500
    assert heat["provenance"]["algorithm"] == "heatmap_data"


@pytest.mark.asyncio
async def test_authoring_failure_drops_data_wrapped_body(
    service, fake_registry, clean_session, monkeypatch,
):
    """#1061(a): #798 只修了成功路径 —— data-wrapped FC（#517 形族）在
    authoring 失败时此前把全量要素体留在 raw_result 里。"""
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.0, 30.7]},
            "properties": {"kind": "school"},
        }],
    }
    fake_registry.dispatch.return_value = {
        "success": True,
        "summary": "poi result",
        "data": feature_collection,
    }
    from app.services.mapspec_store import mapspec_store
    monkeypatch.setattr(
        mapspec_store,
        "layer_upsert",
        AsyncMock(side_effect=RuntimeError("authoring unavailable")),
    )

    result = await service.dispatch(
        _tc("search_poi", {"q": "school"}, tc_id="call_1061a"), clean_session, set()
    )
    assert result.status == "ok"
    assert result.raw_result.get("result_ref") is not None
    assert "data" not in result.raw_result
    assert "features" not in json.dumps(result.raw_result, default=str)


@pytest.mark.asyncio
async def test_corrupt_descriptor_does_not_fail_dispatch(
    service, fake_registry, clean_session, monkeypatch,
):
    """#1061(b): authoring 前置 descriptor 抓取此前未守卫 —— 损坏的
    descriptor JSON（ValueError）会在工具已成功执行、ref 已落库之后把
    整个 dispatch 炸成失败。"""
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.1, 30.6]},
            "properties": {},
        }],
    }
    fake_registry.dispatch.return_value = feature_collection
    from app.services.session_data import session_data_manager
    monkeypatch.setattr(
        session_data_manager,
        "get_ref_descriptor",
        AsyncMock(side_effect=ValueError("Expecting value: line 1 column 1 (char 0)")),
    )
    result = await service.dispatch(
        _tc("search_poi", {"q": "x"}, tc_id="call_1061b"), clean_session, set()
    )
    # 工具已执行成功；descriptor 不可得只应降级元数据，不应整体失败
    assert result.status == "ok"
    assert result.geojson_ref and result.geojson_ref.startswith("ref:")
