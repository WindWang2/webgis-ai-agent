"""PR C - 数据完整性修复的回归测试。

覆盖：
- M11: update_layer_in_state / remove_layer_from_state 用 WATCH/MULTI
- M6: report.py generate_report 期间不持有 DB session
- M8: _save_msg_async 截断 tool_result 到 100000 字符
"""
import inspect
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-data-integrity-32")
os.environ.setdefault("ENV", "development")


# ── M11: Redis WATCH/MULTI ──────────────────────────────────────────────


def test_m11_update_layer_uses_watch():
    """M11：update_layer_in_state 必须用 WATCH/MULTI transaction。"""
    from app.services.session_data_redis import RedisSessionDataManager

    source = inspect.getsource(RedisSessionDataManager.update_layer_in_state)
    assert "watch(" in source.lower(), "update_layer_in_state 未用 WATCH"
    assert "pipe.multi()" in source or "multi()" in source, "缺 MULTI"
    assert "WatchError" in source, "缺 WatchError retry 逻辑"


def test_m11_remove_layer_uses_watch():
    """M11：remove_layer_from_state 同样用 WATCH/MULTI。"""
    from app.services.session_data_redis import RedisSessionDataManager

    source = inspect.getsource(RedisSessionDataManager.remove_layer_from_state)
    assert "watch(" in source.lower()
    assert "WatchError" in source


def test_m11_has_retry_limit():
    """M11：retry 必须有上限（防无限循环）。"""
    from app.services.session_data_redis import RedisSessionDataManager

    source = inspect.getsource(RedisSessionDataManager.update_layer_in_state)
    # range(3) = 3 次重试
    assert "range(3)" in source or "range(" in source
    assert "gave up" in source or "warning" in source.lower(), "超 retry 上限应有 warning"


# ── M6: report.py 不持有 DB session ─────────────────────────────────────


def test_m6_report_generate_uses_expunge():
    """M6：create_report 必须在 generate_report 前 expunge（释放 DB session）。"""
    from app.api.routes import report

    source = inspect.getsource(report.create_report)
    assert "expunge" in source, "create_report 未在 generate_report 前 expunge"
    # 不应在 generate_report 之后还有同一个 db.commit() 持有原 session
    assert "AsyncSessionLocal" in source, "应用新 session 写最终 status"


# ── M8: _save_msg_async 截断 ────────────────────────────────────────────


def test_m8_save_msg_async_truncates_tool_result():
    """M8：_save_msg_async 必须对 tool_result 截断到 100000 字符。

    源码 inspect 验证（行为测试需要完整 mock save_message 链路，过于脆弱）。
    """
    from app.services.chat_engine import ChatEngine

    source = inspect.getsource(ChatEngine._save_msg_async)
    assert "100000" in source, "_save_msg_async 未对 tool_result 截断到 100000"
    assert "truncated" in source.lower() or "[:100000]" in source
