"""F15: chat.py module globals must raise clear error when accessed before initialization."""
import pytest


def test_engine_accessor_raises_before_init(monkeypatch):
    """Accessing engine before lifespan init must raise RuntimeError, not return None."""
    from app.api.routes import chat

    monkeypatch.setattr(chat, 'engine', None)
    monkeypatch.setattr(chat, 'registry', None)

    with pytest.raises(RuntimeError, match="ChatEngine 尚未初始化"):
        chat.get_engine()

    with pytest.raises(RuntimeError, match="ToolRegistry 尚未初始化"):
        chat.get_registry()


def test_engine_accessor_returns_when_set(monkeypatch):
    """Accessing engine after lifespan init must return the instance."""
    from app.api.routes import chat

    sentinel_engine = object()
    sentinel_registry = object()
    monkeypatch.setattr(chat, 'engine', sentinel_engine)
    monkeypatch.setattr(chat, 'registry', sentinel_registry)

    assert chat.get_engine() is sentinel_engine
    assert chat.get_registry() is sentinel_registry
