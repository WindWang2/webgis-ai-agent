"""chat/dispatcher 单测（M1 深水区拆分）：

dispatch_tool / is_suspicious_result。
不打真 LLM、不依赖 ChatEngine — 用 mock registry。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.chat.dispatcher import dispatch_tool, is_suspicious_result
from app.services.session_data import session_data_manager


# ─── is_suspicious_result（纯函数枚举） ──────────────────────


class TestSuspicious:
    @pytest.mark.parametrize(
        "result, expected",
        [
            (None, True),
            ("", True),
            ([], True),
            ({}, True),
            ({"success": False, "code": "X"}, True),
            ({"type": "FeatureCollection", "features": []}, True),
            ({"data": []}, True),
            ({"poi_count": 0}, True),
            ({"type": "FeatureCollection", "features": [{"id": 1}]}, False),
            ({"data": ["x"]}, False),
            ({"poi_count": 5}, False),
            ({"success": True, "ref": "ref:x"}, False),
            (["x"], False),
        ],
    )
    def test_each_shape(self, result, expected):
        assert is_suspicious_result(result) is expected


# ─── dispatch_tool ───────────────────────────────────────


@pytest.fixture
async def clean_session():
    sid = "test-dispatcher-session"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.fixture
def fake_registry():
    return MagicMock(dispatch=AsyncMock())


def _tc(name: str, args: dict, tc_id: str = "call_1") -> dict:
    return {"id": tc_id, "function": {"name": name, "arguments": args}}


@pytest.mark.asyncio
async def test_repeat_intercepted(clean_session, fake_registry):
    executed: set = set()
    tc = _tc("geocode_cn", {"q": "北京"})

    # 第一次：尚未执行 → 走 dispatch
    fake_registry.dispatch.return_value = {"summary": "ok"}
    r1 = await dispatch_tool(tc, clean_session, executed, registry=fake_registry)
    assert r1["repeated"] is False

    # 第二次同名同参数 → 应当被拦截
    r2 = await dispatch_tool(tc, clean_session, executed, registry=fake_registry)
    assert r2["repeated"] is True
    assert "[重复调用拦截]" in r2["llm_payload"]
    # registry.dispatch 只被调一次
    assert fake_registry.dispatch.call_count == 1


@pytest.mark.asyncio
async def test_registry_exception_is_wrapped(clean_session, fake_registry):
    fake_registry.dispatch.side_effect = ValueError("无法找到引用数据 ref:bogus 校验失败")
    out = await dispatch_tool(_tc("x", {}), clean_session, set(), registry=fake_registry)
    assert out["is_error"] is True
    assert "无法找到" in out["llm_payload"] or "参数校验" in out["llm_payload"]
    assert out["geojson_ref"] is None


@pytest.mark.asyncio
async def test_std_error_dict_response(clean_session, fake_registry):
    fake_registry.dispatch.return_value = {
        "success": False,
        "code": "NOT_FOUND",
        "message": "区域不存在",
        "error_type": "KeyError",
    }
    out = await dispatch_tool(_tc("get_district", {"name": "ghost"}), clean_session, set(), registry=fake_registry)
    assert out["is_error"] is True
    assert "区域不存在" in out["error_msg"]
    # tool_failed 写进了 event_log
    log = await session_data_manager.get_event_log(clean_session)
    assert any(e["event"] == "tool_failed" for e in log)


@pytest.mark.asyncio
async def test_success_with_geojson_creates_ref_and_broadcasts(clean_session, fake_registry):
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Point", "coordinates": [116.4, 39.9]}, "properties": {}}],
        "summary": "1 point",
    }
    broadcasts: list[tuple[str, str, dict]] = []
    out = await dispatch_tool(
        _tc("search_poi", {"q": "school"}, tc_id="call_42"),
        clean_session,
        set(),
        registry=fake_registry,
        fire_broadcast=lambda sid, ev, data: broadcasts.append((sid, ev, data)),
    )
    assert out["is_error"] is False
    assert out["geojson_ref"]
    assert out["geojson_ref"].startswith("ref:geojson-")
    # 广播被触发
    assert len(broadcasts) == 1
    bcast = broadcasts[0]
    assert bcast[1] == "geojson_update"
    assert bcast[2]["step_id"] == "call_42"
    assert bcast[2]["geojson"] == out["geojson_ref"]
    # event_log 记录
    log = await session_data_manager.get_event_log(clean_session)
    assert any(e["event"] == "tool_executed" for e in log)


@pytest.mark.asyncio
async def test_success_without_geojson_no_broadcast(clean_session, fake_registry):
    fake_registry.dispatch.return_value = {"summary": "info", "stat": 42}
    broadcasts: list[tuple] = []
    out = await dispatch_tool(
        _tc("spatial_stats", {}, tc_id="call_x"),
        clean_session,
        set(),
        registry=fake_registry,
        fire_broadcast=lambda *a: broadcasts.append(a),
    )
    assert out["geojson_ref"] is None
    assert broadcasts == []


@pytest.mark.asyncio
async def test_suspicious_result_appends_hint(clean_session, fake_registry):
    """空 FeatureCollection 应触发自愈提示尾巴。"""
    fake_registry.dispatch.return_value = {"type": "FeatureCollection", "features": []}
    out = await dispatch_tool(_tc("query_osm_poi", {"area": "..."}), clean_session, set(), registry=fake_registry)
    assert "未返回任何空间要素" in out["llm_payload"]


@pytest.mark.asyncio
async def test_fire_broadcast_None_safe(clean_session, fake_registry):
    """fire_broadcast=None 时不应抛错（subagent / 测试场景）。"""
    fake_registry.dispatch.return_value = {
        "type": "FeatureCollection",
        "features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
    }
    out = await dispatch_tool(_tc("x", {}), clean_session, set(), registry=fake_registry, fire_broadcast=None)
    assert out["geojson_ref"]  # 仍会生成 ref
