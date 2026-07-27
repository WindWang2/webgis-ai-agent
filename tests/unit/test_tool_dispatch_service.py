"""ToolDispatchService 单测（统一工具调度 expand 阶段）：

接口即测试面。通过 dispatch() 的返回值（判别式结果 dataclass）断言可观察结果，
不窥探内部状态。这是 expand 阶段：新服务与旧 dispatcher.py 并存，尚无生产调用方。

复用现有 harness 约定（见 test_chat_dispatcher.py）：clean_session fixture、
fake_registry（MagicMock + AsyncMock）、_tc 工具调用辅助。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.tool_dispatch_service import (
    ToolDispatchResult,
    ToolDispatchService,
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
