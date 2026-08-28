"""Pi-path dispatch 适配器契约测试（unified-tool-dispatch 票据 02）。

锁定的契约：Pi 路径经 ToolDispatchService 调度一次，结果按已验证 session/toolCallId 缓存；
HTTP 回调适配器把 llm_payload 翻成 PiToolResponse.content，SSE 适配器读缓存结果
发 step_result 并携带 geojson_ref。两条适配器共享一次 dispatch。

这是整个 unified-tool-dispatch 工作修复的静默回归的契约锁：此前 Pi 路径从不产生
ref_id，前端图层挂载逻辑（键 off geojson_ref）找不到图层可挂。本测试确保
geojson_ref 从 dispatch 一路 round-trip 到 SSE payload。

Pi 扩展携带后端签发的 turn capability；HTTP route 验签后以其中的 sessionId
作为唯一路由权限。SSE 读取也捕获同一 turn session，因此碰撞的 toolCallId 或
延迟回调不能跨 session 消费结果。
"""
import pytest
from app.tools.registry import ToolRegistry

from app.agent_pi_bridge import (
    PiToolRequest,
    dispatch_tool,
    get_cached_dispatch_result,
    set_tool_registry,
)
from app.agent_pi_bridge import _session_executed_sets, _dispatch_result_cache
from app.services.session_data import session_data_manager
from app.services.chat.pi_event_mapper import map_event_to_sse


@pytest.fixture
async def clean_session():
    sid = "test-pi-dispatch-session"
    await session_data_manager.clear_session(sid)
    # 隔离：清空该 session 的重复拦截集合与 dispatch 缓存（真实流程由 turn 边界清）。
    _session_executed_sets.pop(sid, None)
    _dispatch_result_cache.clear()
    yield sid
    await session_data_manager.clear_session(sid)
    _session_executed_sets.pop(sid, None)
    _dispatch_result_cache.clear()


def _geojson_tool_registry() -> ToolRegistry:
    """注册一个返回 FeatureCollection 的工具（会触发 ref 存储）。"""
    registry = ToolRegistry()

    def make_features(**_):
        return {
            "type": "FeatureCollection",
            "features": [
                {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, "properties": {}}
            ],
            "summary": "1 point",
        }

    registry.register(
        "pi_geo_tool",
        "Produce a feature collection",
        make_features,
        parameters={"type": "object", "properties": {}, "required": []},
    )
    return registry


def _via_execute(tool_call_id: str, tool: str, arguments: dict, sid: str) -> PiToolRequest:
    """扩展真实路径：非 native 工具一律经 webgis_execute 代理调用。"""
    return PiToolRequest(
        toolCallId=tool_call_id,
        name="webgis_execute",
        arguments={"toolName": tool, "arguments": arguments},
        sessionId=sid,
    )


# ─── HTTP-callback 适配器：dispatch_tool → PiToolResponse ──────────


@pytest.mark.asyncio
async def test_http_callback_translates_llm_payload_to_content(clean_session):
    """dispatch_tool 经 ToolDispatchService 调度，返回 PiToolResponse.content 含 llm_payload。"""
    set_tool_registry(_geojson_tool_registry())
    req = _via_execute("tc-geo-1", "pi_geo_tool", {}, clean_session)
    resp = await dispatch_tool(req)
    assert resp.toolCallId == "tc-geo-1"
    assert not resp.isError
    assert resp.content  # 有内容给 Pi 的 LLM 看


@pytest.mark.asyncio
async def test_http_callback_caches_result_for_sse_adapter(clean_session):
    """dispatch 后结果按 toolCallId 缓存，供 SSE 适配器读取。"""
    set_tool_registry(_geojson_tool_registry())
    req = _via_execute("tc-geo-2", "pi_geo_tool", {}, clean_session)
    await dispatch_tool(req)

    cached = get_cached_dispatch_result("tc-geo-2", clean_session)
    assert cached is not None
    assert cached.status == "ok"
    assert cached.geojson_ref is not None  # 关键：ref 被存储了


# ─── SSE 适配器：map_event_to_sse 读缓存 ────────────────


@pytest.mark.asyncio
async def test_sse_adapter_round_trips_geojson_ref(clean_session):
    """【回归锁定核心】SSE step_result 必须携带 geojson_ref。

    此前 Pi 路径的 map_event_to_sse 从 Pi 事件 payload 取 result 再 slim，
    从不携带 geojson_ref（因为 dispatch 没存 ref）。现在读缓存结果，
    geojson_ref 必须 round-trip 进 SSE payload，前端才能挂载图层。
    """
    set_tool_registry(_geojson_tool_registry())
    # 先 dispatch 一次（HTTP 回调路径），缓存结果
    await dispatch_tool(_via_execute("tc-geo-3", "pi_geo_tool", {}, clean_session))

    # Pi 随后流式回传 tool_execution_end 事件 —— SSE 适配器读缓存。
    # 生产路径的 mapper closure 捕获验签后的 clean_session；测试直接传 helper
    # 时，唯一匹配的兼容查找也应命中。
    event = {
        "type": "tool_execution_end",
        "toolCallId": "tc-geo-3",
        "toolName": "pi_geo_tool",
        "result": {},  # 现在被忽略；真相在缓存里
        "isError": False,
    }
    sse = map_event_to_sse(event, clean_session, cache_lookup=get_cached_dispatch_result)
    assert sse is not None
    assert "geojson_ref" in sse
    assert "ref:geojson-" in sse


@pytest.mark.asyncio
async def test_sse_adapter_falls_back_when_no_cache(clean_session):
    """缓存未命中（如 Pi 重复回传、或 dispatch 失败）时不应崩溃。

    退化到旧行为：从事件 payload 取 result slim 一下，但不带 geojson_ref。
    """
    event = {
        "type": "tool_execution_end",
        "toolCallId": "tc-miss",
        "toolName": "some_tool",
        "result": {"summary": "info"},
        "isError": False,
    }
    sse = map_event_to_sse(event, clean_session, cache_lookup=get_cached_dispatch_result)
    assert sse is not None  # 不崩溃
    assert "step_result" in sse


@pytest.mark.asyncio
async def test_sse_adapter_error_uses_cached_error_status(clean_session):
    """dispatch 返回 error 时，SSE 发 step_error 并带缓存的 error_msg。"""
    registry = ToolRegistry()

    def boom(**_):
        raise ValueError("参数校验失败: bad input")

    registry.register("pi_fail_tool", "Always fails", boom,
                      parameters={"type": "object", "properties": {}, "required": []})
    set_tool_registry(registry)

    await dispatch_tool(_via_execute("tc-err-1", "pi_fail_tool", {}, clean_session))

    event = {
        "type": "tool_execution_end",
        "toolCallId": "tc-err-1",
        "toolName": "pi_fail_tool",
        "result": {},
        "isError": False,  # Pi 不知道服务端判定为 error；真相在缓存
    }
    sse = map_event_to_sse(event, clean_session, cache_lookup=get_cached_dispatch_result)
    assert sse is not None
    assert "step_error" in sse


@pytest.mark.asyncio
async def test_dispatch_cache_is_scoped_to_verified_turn_session(clean_session):
    """A delayed callback/result cannot be consumed by another session turn."""
    set_tool_registry(_geojson_tool_registry())
    await dispatch_tool(_via_execute("tc-asym", "pi_geo_tool", {}, clean_session))

    # SSE 适配器在一个拥有真实 session_id 的 turn 中读取。
    event = {
        "type": "tool_execution_end",
        "toolCallId": "tc-asym",
        "toolName": "pi_geo_tool",
        "result": {},
        "isError": False,
    }
    wrong = map_event_to_sse(
        event,
        "another-session",
        cache_lookup=lambda tool_call_id: get_cached_dispatch_result(
            tool_call_id, "another-session"
        ),
    )
    assert wrong is not None
    assert "geojson_ref" not in wrong

    owned = map_event_to_sse(
        event,
        clean_session,
        cache_lookup=lambda tool_call_id: get_cached_dispatch_result(
            tool_call_id, clean_session
        ),
    )
    assert owned is not None
    assert "ref:geojson-" in owned
