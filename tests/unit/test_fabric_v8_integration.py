"""V8 单一生产路径端到端集成（ADR-0130 Phase H 验收）。

离线全链路：connect_data_source（连接即治理）→ query_federated_chain
（runtime 治理解析 + 治理富集）→ 结果披露段断言。

验收映射：
- 同一 registry/query API 服务联邦链（Runtime 三级解析链）；
- explain 携带 pushdown/crs 段（V6 既有）+ estimate_basis（V8 富集披露）；
- 治理 record 不含明文凭证；
- fabric/counters 段如实披露。
"""
import pytest

from app.services.data_fabric.connection_manager import connection_manager
from app.services.data_fabric.fabric.connection_registry import (
    TenantScope,
    get_connection_registry,
    reset_connection_registry,
)
from app.services.data_fabric.fabric.runtime import reset_fabric_runtime
from app.tools.data_fabric_tools import register_data_fabric_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
def tools():
    reset_connection_registry()
    reset_fabric_runtime()
    connection_manager.clear()
    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    yield reg._tools
    reset_connection_registry()
    reset_fabric_runtime()
    connection_manager.clear()


@pytest.mark.asyncio
async def test_connect_then_federated_chain_single_path(tools):
    connect = tools["connect_data_source"]
    chain = tools["query_federated_chain"]

    conn = await connect(
        profile_id="v8_demo",
        source_type="generic",
        session_id="sess-e2e",
        url="https://gis.example.com/api",
        password="topsecret-pw",
        options={"datasets": [{"id": "layer_a", "feature_count": 6},
                              {"id": "layer_b", "feature_count": 6}]},
    )
    assert conn["status"] == "connected"
    assert conn["connection_profile"]["password"] == "********"

    # 治理视图：record 无明文凭证、revision 存在。
    registry = get_connection_registry()
    record = registry.peek("v8_demo", TenantScope(owner="sess-e2e"))
    assert record is not None
    assert record.revision
    assert "topsecret-pw" not in record.model_dump().__str__()

    out = await chain(
        sources=[
            {"dataset_id": "layer_a", "source_id": "s0"},
            {"dataset_id": "layer_b", "source_id": "s1"},
        ],
        joins=[{"kind": "attribute_join", "join_field_left": "id",
                "join_field_right": "id",
                "left_source_id": "s0", "right_source_id": "s1"}],
        limit=50,
        session_id="sess-e2e",
    )
    assert out["status"] == "success", out
    assert out.get("engine") in ("v6", "v5", "v5_fallback")
    # V7 fabric 段（counters/cache 披露）随单一路径可达。
    assert "fabric" in out
    assert "explain" in out
    # V8：explain_v6 携带 estimate_basis 富集披露（source_type 来自 registry
    # record；行数事实来自 descriptor 采集）。
    explain_v6 = "\n".join(out.get("explain_v6") or [])
    if out.get("engine") == "v6":
        assert "estimate_basis:" in explain_v6
        assert "source_facts:" in explain_v6
    assert out.get("row_count") is not None


@pytest.mark.asyncio
async def test_chain_without_connection_returns_typed_error(tools):
    chain = tools["query_federated_chain"]
    out = await chain(
        sources=[{"dataset_id": "ghost_a", "source_id": "s0"},
                 {"dataset_id": "ghost_b", "source_id": "s1"}],
        joins=[{"kind": "attribute_join", "join_field_left": "id",
                "join_field_right": "id"}],
        session_id="sess-none",
    )
    assert out["status"] == "error"
    assert out["error_type"] == "UNSUPPORTED_SOURCE"


@pytest.mark.asyncio
async def test_scope_isolation_through_tool_path(tools):
    connect = tools["connect_data_source"]
    chain = tools["query_federated_chain"]

    await connect(
        profile_id="v8_iso",
        source_type="generic",
        session_id="sess-owner-a",
        url="https://gis.example.com/api",
        options={"datasets": [{"id": "iso_a"}, {"id": "iso_b"}]},
    )
    out = await chain(
        sources=[{"dataset_id": "iso_a", "source_id": "s0"},
                 {"dataset_id": "iso_b", "source_id": "s1"}],
        joins=[{"kind": "attribute_join", "join_field_left": "id",
                "join_field_right": "id"}],
        session_id="sess-owner-b",  # 另一会话：不可见 → typed 错误
    )
    assert out["status"] == "error"
    assert out["error_type"] == "UNSUPPORTED_SOURCE"
