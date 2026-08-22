"""
Unit tests for the 7 Data Fabric AI tools in app/tools/data_fabric_tools.py:
connect_data_source, inspect_data_source, search_spatial_catalog, describe_dataset,
query_dataset, materialize_dataset, refresh_data_source.
"""
import pytest
from app.tools.registry import ToolRegistry
from app.tools.__init__ import init_tools
from app.tools.data_fabric_tools import register_data_fabric_tools


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    return reg


def test_tool_registration(registry):
    tools = registry.list_tools()
    expected = {
        "connect_data_source",
        "inspect_data_source",
        "search_spatial_catalog",
        "describe_dataset",
        "query_dataset",
        "materialize_dataset",
        "refresh_data_source",
    }
    for tool_name in expected:
        assert tool_name in tools, f"Tool '{tool_name}' missing from registry"


def test_init_tools_includes_data_fabric():
    reg = ToolRegistry()
    init_tools(reg)
    tools = reg.list_tools()
    assert "connect_data_source" in tools
    assert "search_spatial_catalog" in tools
    assert "materialize_dataset" in tools


@pytest.mark.asyncio
async def test_connect_and_inspect_tools(registry):
    conn_func = registry._tools["connect_data_source"]
    insp_func = registry._tools["inspect_data_source"]

    res = await conn_func(
        profile_id="unit_pg_test",
        source_type="postgis",
        url="https://gis.example.com/api",
        database="geodb",
        username="admin",
        password="secret_password_123",
    )
    assert res["status"] == "connected"
    assert res["connection_profile"]["password"] == "********"  # Sanitized!
    assert res["datasets_count"] > 0

    insp_res = await insp_func(profile_id="unit_pg_test")
    assert insp_res["status"] == "inspected"
    assert insp_res["profile_id"] == "unit_pg_test"
    assert "pushdown_bbox" in insp_res["capabilities"]


@pytest.mark.asyncio
async def test_search_describe_query_materialize_tools(registry):
    conn_func = registry._tools["connect_data_source"]
    search_func = registry._tools["search_spatial_catalog"]
    desc_func = registry._tools["describe_dataset"]
    query_func = registry._tools["query_dataset"]
    mat_func = registry._tools["materialize_dataset"]
    refresh_func = registry._tools["refresh_data_source"]

    await conn_func(
        profile_id="unit_pg_test",
        source_type="postgis",
        url="https://gis.example.com/api",
        database="geodb",
        username="admin",
        password="secret_password_123",
    )

    # 1. Search
    search_res = search_func(query="unit_pg_test")
    assert search_res["total"] >= 1

    # 2. Describe
    desc_res = desc_func(dataset_id="unit_pg_test", profile_id="unit_pg_test")
    assert desc_res["dataset_id"] == "unit_pg_test"
    assert "fingerprint" in desc_res

    # 3. Query
    query_res = await query_func(dataset_id="unit_pg_test", limit=5, profile_id="unit_pg_test")
    assert query_res["dataset_id"] == "unit_pg_test"
    assert len(query_res["features"]) > 0

    # 4. Materialize
    mat_res = await mat_func(
        dataset_id="unit_pg_test",
        session_id="tool_test_session",
        layer_name="Tool Materialized Layer",
        profile_id="unit_pg_test",
    )
    assert mat_res["status"] == "success"
    assert mat_res["ref_id"].startswith("ref:data-fabric-")

    # 5. Refresh
    ref_res = await refresh_func(profile_id="unit_pg_test")
    assert ref_res["status"] == "refreshed"
    assert ref_res["profile_id"] == "unit_pg_test"


@pytest.mark.asyncio
async def test_connect_ssrf_blocking(registry):
    conn_func = registry._tools["connect_data_source"]
    with pytest.raises(Exception):
        await conn_func(
            profile_id="malicious_profile",
            source_type="wfs",
            url="http://127.0.0.1/admin",
        )


# ─── #767 / #768: no fabricated descriptors, no synthetic masquerade ────────


def test_describe_dataset_unknown_id_is_typed_error_768(registry):
    """#768: an unknown dataset id must return a typed DATASET_NOT_FOUND error
    — previously a fabricated worldwide Polygon/EPSG:4326 descriptor was
    returned with a deterministic fingerprint as if it were real schema."""
    desc_func = registry._tools["describe_dataset"]
    res = desc_func(dataset_id="no-such-dataset-768")
    assert res["status"] == "error"
    assert res["error_type"] == "DATASET_NOT_FOUND"
    assert "no-such-dataset-768" in res["error"]
    # No fabricated descriptor or fingerprint computed.
    assert "descriptor" not in res
    assert "fingerprint" not in res


@pytest.mark.asyncio
async def test_connect_demo_source_labeled_767(registry):
    """#767: the connect tool result labels demo/sample adapters with
    is_demo=True (and real adapters False), and unregistered types are
    rejected loudly instead of fabricating an adapter."""
    conn_func = registry._tools["connect_data_source"]

    res = await conn_func(
        profile_id="unit_demo_767", source_type="generic", url="https://example.com/api"
    )
    assert res["status"] == "connected"
    assert res["is_demo"] is True

    res_real = await conn_func(
        profile_id="unit_real_767", source_type="postgis", url="https://example.com/api"
    )
    assert res_real["is_demo"] is False

    with pytest.raises(Exception):  # UnsupportedSourceError from the registry
        await conn_func(
            profile_id="unit_csv_767", source_type="csv", url="https://example.com/api"
        )
