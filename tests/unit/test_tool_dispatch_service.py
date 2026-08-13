"""ToolDispatchService 单测（统一工具调度）：

接口即测试面。通过 dispatch() 的返回值（判别式结果 dataclass）断言可观察结果，
不窥探内部状态。两条 agent 路径（legacy ChatEngine + Pi bridge）共用本服务。

harness 约定：clean_session fixture、fake_registry（MagicMock + AsyncMock）、
_tc 工具调用辅助。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.tool_dispatch_service import (
    ToolDispatchResult,
    ToolDispatchService,
    normalize_tool_name,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = "test-dispatch-service-session"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


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
    """断言 legacy 工具名被正确映射为 webgis_* canonical 名称。"""
    assert normalize_tool_name("add_layer") == "webgis_layer_upsert"
    assert normalize_tool_name("set_layer_style") == "webgis_layer_upsert"
    assert normalize_tool_name("set_view") == "webgis_view_set"
    assert normalize_tool_name("remove_layer") == "webgis_layer_remove"
    assert normalize_tool_name("init_project") == "webgis_project_init"
    assert normalize_tool_name("unknown_tool") == "unknown_tool"


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

    # 原调用完成后，同参再调 → post-success 文案（保持原文案，含"成功执行"）
    r2 = await service.dispatch(tc, clean_session, executed)
    assert r2.status == "repeated"
    assert "以相同参数成功执行" in r2.llm_payload
