"""Explorer — 意图历史去重（P4 补强 E4：_check_recent_history 触发路径）。"""
from __future__ import annotations

from app.services.explorer.intent_detector import IntentDetector


def _detector() -> IntentDetector:
    return IntentDetector()


def test_empty_history_never_dedupes() -> None:
    assert _detector()._check_recent_history([], "找一下成都的火锅店") is False


def test_explorer_ref_in_recent_history_triggers_dedupe() -> None:
    history = [
        {"role": "user", "content": "找一下成都的火锅店"},
        {"role": "assistant", "content": "已完成深度探索，结果 ref:explorer_abc123 已挂载"},
    ]
    assert _detector()._check_recent_history(history, "再来一次附近的搜索") is True


def test_deep_search_phrase_triggers_dedupe() -> None:
    history = [{"role": "assistant", "content": "正在执行深度搜索，请稍候"}]
    assert _detector()._check_recent_history(history, "换个关键词") is True


def test_unrelated_history_does_not_trigger() -> None:
    history = [
        {"role": "user", "content": "帮我做一个缓冲区分析"},
        {"role": "assistant", "content": "缓冲区已完成"},
    ]
    assert _detector()._check_recent_history(history, "找一下成都的火锅店") is False


def test_only_recent_three_messages_are_considered() -> None:
    old_explorer = {"role": "assistant", "content": "ref:explorer_old"}
    recent_plain = [
        {"role": "user", "content": "普通问题一"},
        {"role": "assistant", "content": "普通回答一"},
        {"role": "user", "content": "普通问题二"},
    ]
    history = [old_explorer, *recent_plain]
    # ref:explorer_ 在第 4 条（更早），不在最近 3 条窗口内 → 不触发。
    assert _detector()._check_recent_history(history, "继续探索") is False
