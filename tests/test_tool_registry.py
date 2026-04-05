"""工具注册框架测试"""
import pytest
import asyncio
from app.tools.registry import ToolRegistry, tool


def test_register_and_list():
    registry = ToolRegistry()

    @tool(registry, name="test_tool", description="A test tool")
    def my_tool(query: str) -> dict:
        return {"result": query}

    assert "test_tool" in registry.list_tools()


def test_get_schemas():
    registry = ToolRegistry()

    @tool(registry, name="geocode", description="Geocode a location",
           param_descriptions={"query": "Location name"})
    def geocode(query: str) -> dict:
        return {"lat": 0, "lon": 0}

    schemas = registry.get_schemas()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "geocode"
    assert "query" in s["function"]["parameters"]["properties"]
    assert s["function"]["parameters"]["required"] == ["query"]


@pytest.mark.asyncio
async def test_dispatch_sync():
    registry = ToolRegistry()

    @tool(registry, name="add", description="Add numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = await registry.dispatch("add", {"a": 3, "b": 5})
    assert result == 8


@pytest.mark.asyncio
async def test_dispatch_async():
    registry = ToolRegistry()

    @tool(registry, name="async_echo", description="Async echo")
    async def async_echo(msg: str) -> str:
        return msg

    result = await registry.dispatch("async_echo", {"msg": "hello"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_dispatch_with_json_string():
    registry = ToolRegistry()

    @tool(registry, name="echo", description="Echo")
    def echo(msg: str) -> str:
        return msg

    result = await registry.dispatch("echo", '{"msg": "hello"}')
    assert result == "hello"


@pytest.mark.asyncio
async def test_dispatch_unknown_raises():
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        await registry.dispatch("nonexistent", {})


def test_optional_params_not_required():
    registry = ToolRegistry()

    @tool(registry, name="search", description="Search")
    def search(query: str, limit: int = 10) -> list:
        return []

    schemas = registry.get_schemas()
    required = schemas[0]["function"]["parameters"]["required"]
    assert "query" in required
    assert "limit" not in required
