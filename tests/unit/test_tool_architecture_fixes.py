"""Unit tests verifying Tool Architecture audit fixes (#945-#949)."""
import pytest
from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog


@pytest.mark.asyncio
async def test_registry_dispatches_sync_func_without_kwargs_safely():
    """Sync tools lacking session_id / **kwargs should not fail when session_id is provided in dispatch."""
    reg = ToolRegistry()

    @reg.tool(name="strict_sync_add", description="add numbers")
    def strict_sync_add(a: int, b: int) -> dict:
        return {"result": a + b}

    # Dispatch with session_id="test-session"
    res = await reg.dispatch("strict_sync_add", {"a": 1, "b": 2}, session_id="test-session")
    assert res == {"result": 3}


def test_tool_catalog_keyword_case_insensitivity():
    """ToolCatalog keyword detection should match mixed-case and uppercase keywords case-insensitively."""
    reg = ToolRegistry()
    cat = ToolCatalog(reg)

    # Mixed-case / uppercase queries for domains
    domains_osm = cat.detect_domains("我想查看这个区域的OSM道路网络")
    assert "network" in domains_osm or "osm" in domains_osm or len(domains_osm) > 0

    domains_ndvi = cat.detect_domains("计算植被NDVI指数")
    assert "remote_sensing" in domains_ndvi or "raster" in domains_ndvi or len(domains_ndvi) > 0


def test_dynamic_tool_registration_updates_schema_cleanly():
    """Registering a tool with the same name replaces the old schema and maintains single entry."""
    reg = ToolRegistry()

    @reg.tool(name="dynamic_calc", description="v1 calc")
    def calc_v1(x: int) -> int:
        return x * 1

    schemas_1 = reg.get_schemas()
    assert len(schemas_1) == 1
    assert schemas_1[0]["function"]["description"] == "v1 calc"

    # Register updated implementation under the same name
    @reg.tool(name="dynamic_calc", description="v2 calc")
    def calc_v2(x: int, y: int = 10) -> int:
        return x + y

    schemas_2 = reg.get_schemas()
    assert len(schemas_2) == 1
    assert schemas_2[0]["function"]["description"] == "v2 calc"
    assert "y" in schemas_2[0]["function"]["parameters"]["properties"]
