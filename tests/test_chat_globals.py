"""F15: chat.py module globals must raise clear error when accessed before initialization.

审计 S47：之前 raise RuntimeError -> 全局 exception handler 返回 500 + 可能
泄漏内部模块名。改为 HTTPException(503) 让客户端知道是启动窗口临时不可用。
"""
import pytest
from fastapi import HTTPException


def test_engine_accessor_raises_before_init(monkeypatch):
    """Accessing engine before lifespan init must raise HTTPException(503), not RuntimeError."""
    from app.api.routes import chat

    monkeypatch.setattr(chat, 'engine', None)
    monkeypatch.setattr(chat, 'registry', None)

    with pytest.raises(HTTPException) as exc_info:
        chat.get_engine()
    assert exc_info.value.status_code == 503

    with pytest.raises(HTTPException) as exc_info:
        chat.get_registry()
    assert exc_info.value.status_code == 503


def test_engine_accessor_returns_when_set(monkeypatch):
    """Accessing engine after lifespan init must return the instance."""
    from app.api.routes import chat

    sentinel_engine = object()
    sentinel_registry = object()
    monkeypatch.setattr(chat, 'engine', sentinel_engine)
    monkeypatch.setattr(chat, 'registry', sentinel_registry)

    assert chat.get_engine() is sentinel_engine
    assert chat.get_registry() is sentinel_registry
