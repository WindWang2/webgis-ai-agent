"""Round 5: 历史压缩 — token 估算 + 轮次切分 + 预算截断"""
import pytest

from app.services.session_data import session_data_manager
from app.services.chat.context_builder import (
    _estimate_tokens,
    _group_into_turns,
    _message_tokens,
    compose_request_messages,
    truncate_history_by_budget,
    HISTORY_MIN_TURNS,
)


def test_estimate_tokens_ascii():
    # ~ len/4 + 1
    assert 19 <= _estimate_tokens("a" * 80) <= 25


def test_estimate_tokens_cjk_heavier():
    cjk = _estimate_tokens("字" * 20)
    ascii_ = _estimate_tokens("a" * 20)
    assert cjk > ascii_


def test_estimate_tokens_handles_dict():
    n = _estimate_tokens({"key": "value"})
    assert n > 0


def test_message_tokens_includes_tool_calls():
    base = _message_tokens({"role": "assistant", "content": "hi"})
    with_tc = _message_tokens({
        "role": "assistant",
        "content": "hi",
        "tool_calls": [{"id": "1", "function": {"name": "x", "arguments": "{}"}}],
    })
    assert with_tc > base


def test_group_into_turns_basic():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "tool", "content": "r1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    turns = _group_into_turns(msgs)
    assert len(turns) == 2
    assert [m["role"] for m in turns[0]] == ["user", "assistant", "tool"]
    assert [m["role"] for m in turns[1]] == ["user", "assistant"]


def test_truncate_under_budget_returns_unchanged():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    kept, dropped = truncate_history_by_budget(msgs, budget=10_000)
    assert dropped == 0
    assert kept == msgs


def test_truncate_drops_oldest_turns():
    msgs = []
    for i in range(8):
        msgs.append({"role": "user", "content": "Q" * 800})
        msgs.append({"role": "assistant", "content": "A" * 800})
    kept, dropped = truncate_history_by_budget(msgs, budget=800)
    assert dropped > 0
    # 截断后第一条还是 user，保持 turn 完整性
    assert kept[0]["role"] == "user"
    # 最新一轮一定保留
    assert kept[-1] == msgs[-1]


def test_truncate_preserves_min_turns_even_if_over_budget():
    # 每条都很大，但 min_turns 是硬底线
    msgs = []
    for _ in range(5):
        msgs.append({"role": "user", "content": "Q" * 10_000})
        msgs.append({"role": "assistant", "content": "A" * 10_000})
    kept, dropped = truncate_history_by_budget(msgs, budget=100, min_turns=2)
    turns_kept = _group_into_turns(kept)
    assert len(turns_kept) == HISTORY_MIN_TURNS == 2
    assert dropped == 3


def test_truncate_handles_empty_history():
    kept, dropped = truncate_history_by_budget([], budget=1000)
    assert kept == []
    assert dropped == 0


def test_compose_injects_truncation_notice_when_dropping():
    sid = "comp-trunc"
    session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")

    msgs = [{"role": "system", "content": "BASE"}]
    for _ in range(20):
        msgs.append({"role": "user", "content": "Q" * 2000})
        msgs.append({"role": "assistant", "content": "A" * 2000})

    out = compose_request_messages(sid, msgs)
    notice_msgs = [m for m in out if m.get("role") == "system" and "历史折叠" in m.get("content", "")]
    assert len(notice_msgs) == 1
    # 折叠说明里有具体丢弃轮次数
    assert "轮对话" in notice_msgs[0]["content"]
    session_data_manager.clear_session(sid)


def test_compose_no_notice_when_under_budget():
    sid = "comp-ok"
    session_data_manager.set_map_state(sid, "viewport", {"center": [0, 0], "zoom": 5})
    session_data_manager.set_map_state(sid, "base_layer", "OSM 地图")

    msgs = [
        {"role": "system", "content": "BASE"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]
    out = compose_request_messages(sid, msgs)
    assert not any("历史折叠" in m.get("content", "") for m in out if m.get("role") == "system")
    session_data_manager.clear_session(sid)
