"""Public timeout settings reach the engine while legacy deployments keep working."""
import pytest

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "public,legacy,expected",
    [
        (None, None, 5.0),
        ("0.125", None, 0.125),
        (None, "0.25", 0.25),
        ("0.125", "0.25", 0.125),
    ],
)
def test_engine_timeout_environment_names(monkeypatch, public, legacy, expected):
    for name in ("CLEAR_QUIESCE_TIMEOUT", "CANCEL_WAIT_TIMEOUT"):
        for key, value in ((name, legacy), (f"{name}_S", public)):
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
    engine = ChatEngine(ToolRegistry())
    assert engine._clear_quiesce_timeout == expected
    assert engine._cancel_wait_timeout == expected
