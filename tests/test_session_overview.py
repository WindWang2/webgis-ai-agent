"""Round 6: session 元信息概览注入"""
from datetime import datetime, timedelta

import pytest

from app.services.session_data import session_data_manager
from app.services.chat.context_builder import (
    _format_duration,
    build_session_overview,
    compose_request_messages,
)


def test_format_duration_under_1_min():
    iso = (datetime.now() - timedelta(seconds=10)).isoformat()
    assert _format_duration(iso) == "<1 分钟"


def test_format_duration_minutes():
    iso = (datetime.now() - timedelta(minutes=23)).isoformat()
    out = _format_duration(iso)
    assert "23" in out and "分钟" in out


def test_format_duration_hours():
    iso = (datetime.now() - timedelta(hours=2, minutes=5)).isoformat()
    out = _format_duration(iso)
    assert "小时" in out


def test_format_duration_days():
    iso = (datetime.now() - timedelta(days=3)).isoformat()
    out = _format_duration(iso)
    assert "天" in out


def test_format_duration_handles_bad_input():
    assert _format_duration(None) is None
    assert _format_duration("not iso") is None


async def test_session_data_manager_records_started_at():
    sid = "overview-start"
    assert await session_data_manager.get_started_at(sid) is None
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    started = await session_data_manager.get_started_at(sid)
    assert started is not None
    # 不会被后续写入覆盖
    original = started
    await session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    assert await session_data_manager.get_started_at(sid) == original
    await session_data_manager.clear_session(sid)


async def test_session_data_manager_started_at_set_by_store():
    sid = "overview-store-only"
    await session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    assert await session_data_manager.get_started_at(sid) is not None
    await session_data_manager.clear_session(sid)


async def test_build_session_overview_empty_session_returns_none():
    sid = "overview-empty"
    assert await build_session_overview(sid, []) is None
    await session_data_manager.clear_session(sid)


async def test_build_session_overview_counts_turns_tools_errors_exports_refs():
    sid = "overview-counts"
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    await session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="t")
    await session_data_manager.append_event(sid, "tool_executed", {"tool": "buffer"})
    await session_data_manager.append_event(sid, "tool_executed", {"tool": "osm_search", "is_error": True})
    await session_data_manager.append_event(sid, "tool_executed", {"tool": "export_thematic_map", "command": "export_map"})

    msgs = [
        {"role": "system", "content": "BASE"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    overview = await build_session_overview(sid, msgs)
    assert overview is not None
    assert "2 轮提问" in overview
    assert "3 次工具" in overview
    assert "1 次失败" in overview
    assert "1 个数据引用" in overview
    assert "1 张已导出地图" in overview
    await session_data_manager.clear_session(sid)


async def test_compose_request_messages_appends_overview_line():
    sid = "overview-compose"
    await session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    await session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")
    await session_data_manager.append_event(sid, "tool_executed", {"tool": "buffer"})

    msgs = [
        {"role": "system", "content": "BASE"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好"},
    ]
    out = await compose_request_messages(sid, msgs)
    sys_content = out[0]["content"]
    assert "会话概览" in sys_content
    assert "1 轮提问" in sys_content
    await session_data_manager.clear_session(sid)


async def test_compose_omits_overview_when_nothing_happened_yet():
    sid = "overview-bare"
    msgs = [
        {"role": "system", "content": "BASE"},
        {"role": "user", "content": "你好"},
    ]
    out = await compose_request_messages(sid, msgs)
    sys_content = out[0]["content"]
    # 全新 session 还没存 map_state/refs/events，只有 1 轮提问 — 仍应出现 overview
    # 但若把 messages 也清空（极端边界），就该 omit
    out2 = await compose_request_messages(sid, [{"role": "system", "content": "BASE"}])
    assert "会话概览" not in out2[0]["content"]
    await session_data_manager.clear_session(sid)
