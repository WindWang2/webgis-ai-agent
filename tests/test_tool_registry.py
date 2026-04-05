"""工具注册框架测试"""
import pytest
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
    assert s["function"]["description"] == "Geocode a location"
    assert "query" in s["function"]["parameters"]["properties"]
    assert s["function"]["parameters"]["required"] == ["query"]
    assert s["function"]["parameters"]["properties"]["query"]["description"] == "Location name"


def test_dispatch():
    registry = ToolRegistry()

    @tool(registry, name="add", description="Add numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = registry.dispatch("add", {"a": 3, "b": 5})
    assert result == 8


def test_dispatch_with_json_string():
    registry = ToolRegistry()

    @tool(registry, name="echo", description="Echo")
    def echo(msg: str) -> str:
        return msg

    result = registry.dispatch("echo", '{"msg": "hello"}')
    assert result == "hello"


def test_dispatch_unknown_raises():
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        registry.dispatch("nonexistent", {})


def test_optional_params_not_required():
    registry = ToolRegistry()

    @tool(registry, name="search", description="Search")
    def search(query: str, limit: int = 10) -> list:
        return []

    schemas = registry.get_schemas()
    required = schemas[0]["function"]["parameters"]["required"]
    assert "query" in required
    assert "limit" not in required
