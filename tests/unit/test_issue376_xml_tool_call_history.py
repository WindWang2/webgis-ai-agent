"""Issue #376 回归测试：MiniMax XML 工具调用路径必须保持持久化历史 API-valid。

修复前：XML 路径把解析出的 tool_calls 随 assistant 消息落库，但工具响应只
作为内存中的合成 user 消息存在、从不持久化 —— 进程重启 / LRU 逐出后重放
历史产生没有配对 tool 响应的 assistant.tool_calls，OpenAI 兼容 provider 拒绝
后续 turn 的首次 LLM 调用，会话永久损坏。

修复（两层）：
- 写入路径（execution_engine）：XML 分支持久化的 assistant 行只带
  standard_calls（XML 路径为空 → tool_calls=None，降级为普通文本行）；
- 重放路径（history_service_async）：load_context 剥除孤儿 tool_calls
  （兜底修复历史损坏的会话），并把 dict 型 arguments 规范化为 JSON 字符串。
"""
import json

import pytest
from unittest.mock import AsyncMock, patch

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry, tool

XML_TOOL_CONTENT = (
    "开始分析\n"
    'minimax:tool_call <invoke name="geocode"><parameter name="query">北京</parameter></invoke>'
)


@pytest.fixture
def registry():
    r = ToolRegistry()

    @tool(r, name="geocode", description="Geocode")
    def geocode(query: str) -> dict:
        return {"lat": 39.9042, "lon": 116.4074, "name": "北京"}

    return r


def _assistant_saves(mock_save):
    """提取所有 role='assistant' 的 _save_msg_async 调用。"""
    return [
        c for c in mock_save.call_args_list
        if len(c.args) > 1 and c.args[1] == "assistant"
    ]


# ─── 非流式路径 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_xml_tool_turn_persists_assistant_without_tool_calls(registry):
    """非流式 XML 轮：assistant 行以 tool_calls=None 落库（XML 已剥除）。"""
    engine = ChatEngine(registry)
    resp1 = {"choices": [{"message": {"content": XML_TOOL_CONTENT}}]}  # 无 standard tool_calls
    resp2 = {"choices": [{"message": {"content": "北京的坐标是 39.9042, 116.4074"}}]}

    async def fake_load(session_id, user_id=None):
        # F23：本环境无可用 DB，显式 stub 加载成功，否则会话不会进 _sessions 缓存
        return [{"role": "system", "content": "sys"}]

    with patch.object(engine, "_load_session_from_db", side_effect=fake_load):
        with patch.object(engine, "_call_llm", new_callable=AsyncMock, side_effect=[resp1, resp2]):
            with patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save:
                result = await engine.chat("北京的坐标在哪？", session_id="sess-376-xml")

    assert "39.9042" in result["content"]  # 工具确实执行了

    saves = _assistant_saves(mock_save)
    assert len(saves) == 2  # XML 工具轮 + 最终回答轮
    xml_round = saves[0]
    assert xml_round.args[3] is None  # tool_calls 不落库（此前是 xml_calls 列表）
    assert "minimax:tool_call" not in xml_round.args[2]  # XML 标签已从 content 剥除

    # 内存 messages 里的 XML 轮 assistant 条目同样不带 tool_calls
    for m in engine._sessions["sess-376-xml"]:
        if m.get("role") == "assistant":
            assert not m.get("tool_calls")


# ─── 流式路径 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_stream_xml_tool_turn_persists_assistant_without_tool_calls(registry, monkeypatch):
    """流式 XML 轮：assistant 行同样以 tool_calls=None 落库。"""
    engine = ChatEngine(registry)

    async def fake_stream_1(*args, **kwargs):
        yield ("done", {"message": {"content": XML_TOOL_CONTENT}})

    async def fake_stream_2(*args, **kwargs):
        yield ("done", {"message": {"content": "北京的坐标是 39.9042, 116.4074"}})

    async def fake_get_or_create_session(session_id, user_id=None):
        return []

    async def fake_maybe_plan(*a, **kw):
        return None

    async def fake_generate_title(*a, **kw):
        return None

    monkeypatch.setattr(engine, "_get_or_create_session", fake_get_or_create_session)
    monkeypatch.setattr(engine, "_maybe_plan", fake_maybe_plan)
    monkeypatch.setattr(engine, "_generate_title", fake_generate_title)

    with patch.object(
        engine, "_call_llm_stream", side_effect=[fake_stream_1(), fake_stream_2()]
    ):
        with patch.object(engine, "_save_msg_async", new_callable=AsyncMock) as mock_save:
            events = []
            async for event in engine.chat_stream("北京的坐标在哪？", session_id="sess-376-stream"):
                events.append(event)

    assert any("task_complete" in e for e in events)

    saves = _assistant_saves(mock_save)
    assert len(saves) == 2
    xml_round = saves[0]
    assert xml_round.args[3] is None  # tool_calls 不落库
    assert "minimax:tool_call" not in xml_round.args[2]


# ─── 重放路径：孤儿修复（兜底） ─────────────────────────────────


def _conv_with_messages(msgs):
    from app.models.db_model import Conversation

    conv = Conversation(id="sess-376-repair")
    conv.messages = msgs
    return conv


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]


class _FakeDb:
    """最小 AsyncSession 替身：get_or_create_conversation 的 SELECT 直接返回 conv。"""

    def __init__(self, conv):
        self._conv = conv

    async def execute(self, stmt, *a, **k):
        return _FakeResult([self._conv])

    def add(self, *a, **k):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_load_context_strips_orphaned_xml_tool_calls():
    """修复前旧代码写入的行（XML tool_calls + 从不存在的 tool 响应）重放时被
    降级为普通文本 —— 不再产生会拒绝后续 LLM 调用的孤儿 tool_calls。"""
    from app.models.db_model import Message
    from app.services.history_service_async import AsyncHistoryService

    legacy_tc = [{
        "id": "call_xml_1",
        "type": "function",
        "function": {"name": "geocode", "arguments": {"query": "北京"}},
    }]
    msgs = [
        Message(id=1, role="user", content="北京的坐标在哪？"),
        Message(id=2, role="assistant", content="开始分析", tool_calls=legacy_tc),
    ]
    svc = AsyncHistoryService(db=_FakeDb(_conv_with_messages(msgs)))
    ctx = await svc.load_context("sess-376-repair")

    assistant = [m for m in ctx.llm_messages if m["role"] == "assistant"][0]
    assert "tool_calls" not in assistant  # 孤儿 tool_calls 被剥除
    assert assistant["content"] == "开始分析"  # content 保留


@pytest.mark.asyncio
async def test_load_context_keeps_paired_tool_calls_and_normalizes_arguments():
    """已配对的 tool_calls 原样保留；dict 型 arguments 规范化为 JSON 字符串。"""
    from app.models.db_model import Message
    from app.services.history_service_async import AsyncHistoryService

    tc = [{
        "id": "call_xml_1",
        "type": "function",
        "function": {"name": "geocode", "arguments": {"query": "北京"}},
    }]
    msgs = [
        Message(id=1, role="user", content="北京的坐标在哪？"),
        Message(id=2, role="assistant", content="开始分析", tool_calls=tc),
        Message(id=3, role="tool", content='{"lat": 39.9042}', tool_call_id="call_xml_1"),
    ]
    svc = AsyncHistoryService(db=_FakeDb(_conv_with_messages(msgs)))
    ctx = await svc.load_context("sess-376-paired")

    assistant = [m for m in ctx.llm_messages if m["role"] == "assistant"][0]
    kept = assistant["tool_calls"]
    assert len(kept) == 1
    assert isinstance(kept[0]["function"]["arguments"], str)  # dict → JSON 字符串
    assert json.loads(kept[0]["function"]["arguments"]) == {"query": "北京"}

    tool = [m for m in ctx.llm_messages if m["role"] == "tool"][0]
    assert tool["tool_call_id"] == "call_xml_1"


@pytest.mark.asyncio
async def test_load_context_keeps_only_answered_tool_calls():
    """部分配对：只保留有 tool 响应的 tool_call，孤儿剥除。"""
    from app.models.db_model import Message
    from app.services.history_service_async import AsyncHistoryService

    tc = [
        {"id": "call_1", "type": "function", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}},
        {"id": "call_2", "type": "function", "function": {"name": "geocode", "arguments": '{"query": "上海"}'}},
    ]
    msgs = [
        Message(id=1, role="user", content="两城"),
        Message(id=2, role="assistant", content="开始", tool_calls=tc),
        Message(id=3, role="tool", content="ok", tool_call_id="call_2"),
    ]
    svc = AsyncHistoryService(db=_FakeDb(_conv_with_messages(msgs)))
    ctx = await svc.load_context("sess-376-partial")

    assistant = [m for m in ctx.llm_messages if m["role"] == "assistant"][0]
    assert [t["id"] for t in assistant["tool_calls"]] == ["call_2"]


@pytest.mark.asyncio
async def test_load_context_keeps_standard_fc_sequence_untouched():
    """标准 OpenAI FC 序列（字符串 arguments + tool 响应）原样重放，不回归。"""
    from app.models.db_model import Message
    from app.services.history_service_async import AsyncHistoryService

    tc = [{"id": "call_1", "type": "function", "function": {"name": "geocode", "arguments": '{"query": "北京"}'}}]
    msgs = [
        Message(id=1, role="user", content="北京的坐标在哪？"),
        Message(id=2, role="assistant", content="", tool_calls=tc),
        Message(id=3, role="tool", content='{"lat": 39.9042}', tool_call_id="call_1"),
    ]
    svc = AsyncHistoryService(db=_FakeDb(_conv_with_messages(msgs)))
    ctx = await svc.load_context("sess-376-standard")

    assistant = [m for m in ctx.llm_messages if m["role"] == "assistant"][0]
    assert assistant["tool_calls"] == tc  # 原样保留
    assert [m["role"] for m in ctx.llm_messages] == ["user", "assistant", "tool"]
