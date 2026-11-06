"""Server-owned session injection preserves optional defaults and required denial."""
import pytest

from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,default", [("required", None), ("named", "default"), ("empty", ""), ("nullable", None)])
@pytest.mark.parametrize("trusted", [None, "trusted-session"])
async def test_session_context_never_falls_back_to_client_but_preserves_declared_defaults(kind, default, trusted):
    called = []

    async def required(session_id: str):
        called.append(session_id)
        return {"session": session_id}

    async def named(session_id: str = "default"):
        called.append(session_id)
        return {"session": session_id}

    async def empty(session_id: str = ""):
        called.append(session_id)
        return {"session": session_id}

    async def nullable(session_id: str | None = None):
        called.append(session_id)
        return {"session": session_id}

    registry = ToolRegistry()
    registry.register("session-test", "Session authority test", locals()[kind])
    schema = registry.get_schemas()[0]["function"]["parameters"]
    assert "session_id" not in schema["properties"]
    for arguments in ({}, {"session_id": "client-victim"}):
        result = await registry.dispatch("session-test", arguments, session_id=trusted)
        if kind == "required" and trusted is None:
            assert result["success"] is False
            assert result["code"] == "VALIDATION_ERROR"
            assert "session_id" in result["message"]
            assert called == []
        else:
            expected = trusted if trusted is not None else default
            assert result == {"session": expected}
            assert called[-1] == expected
        assert arguments == {} or arguments == {"session_id": "client-victim"}
