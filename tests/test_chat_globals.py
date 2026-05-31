"""F15: chat.py module globals must raise clear error when accessed before initialization."""
import pytest


def test_engine_accessor_raises_before_init():
    """Accessing engine before lifespan init must raise RuntimeError, not return None."""
    from app.api.routes import chat

    orig_engine = chat.engine
    orig_registry = chat.registry

    try:
        chat.engine = None
        chat.registry = None

        with pytest.raises(RuntimeError, match="ChatEngine 尚未初始化"):
            chat.get_engine()

        with pytest.raises(RuntimeError, match="ToolRegistry 尚未初始化"):
            chat.get_registry()
    finally:
        chat.engine = orig_engine
        chat.registry = orig_registry


def test_engine_accessor_returns_when_set():
    """Accessing engine after lifespan init must return the instance."""
    from app.api.routes import chat

    orig_engine = chat.engine
    orig_registry = chat.registry

    try:
        sentinel_engine = object()
        sentinel_registry = object()
        chat.engine = sentinel_engine
        chat.registry = sentinel_registry

        assert chat.get_engine() is sentinel_engine
        assert chat.get_registry() is sentinel_registry
    finally:
        chat.engine = orig_engine
        chat.registry = orig_registry
