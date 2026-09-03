"""V2 agent 工具测试（ADR-0094 §11）：plan/aggregate/federated/query extras。"""
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.data_fabric_schema import DatasetDescriptor, QueryResult
from app.services.data_fabric.query.federation import FederatedQueryRequest
from app.services.data_fabric.spatial_catalog import SpatialCatalogService


def _fake_adapter(result=None, error=None, descriptor=None):
    adapter = MagicMock()
    adapter.profile.source_type = "postgis"
    adapter.describe.return_value = descriptor or DatasetDescriptor(
        id="schools", source_type="postgis", feature_count=1000,
        fields=[{"name": "district", "type": "text"}, {"name": "students", "type": "int"}],
    )
    if error is not None:
        adapter.query.side_effect = error
    else:
        adapter.query.return_value = result or QueryResult(
            dataset_id="schools",
            features=[],
            data=[{"district": "金牛", "count": 12}],
            total_count=1,
            result_mode="statistics",
            metadata={
                "query_plan": {"pushed_aggregation": True},
                "query_evidence": {"query_fingerprint": "abc123", "rows_fetched": 20, "rows_returned": 20},
            },
        )
    return adapter


def _registered_tools():
    from app.tools.registry import ToolRegistry
    from app.tools.data_fabric_tools import register_data_fabric_tools
    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    return reg


def _run(reg, name, **kwargs):
    tool_obj = reg.get_tool(name) if hasattr(reg, "get_tool") else reg._tools[name]
    fn = tool_obj.fn if hasattr(tool_obj, "fn") else tool_obj
    import asyncio
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(fn(**kwargs)) \
        if False else asyncio.run(fn(**kwargs))


@pytest.fixture
def v2_catalog(monkeypatch):
    """patch 工具模块内的 spatial_catalog_service 单例（真实模块级引用）。"""
    import app.tools.data_fabric_tools as tools_mod

    svc = SpatialCatalogService()
    svc.register_dataset(
        DatasetDescriptor(id="schools", source_type="postgis", feature_count=1000,
                          fields=[{"name": "district"}, {"name": "students"}]),
        profile_id="pg1",
    )
    svc.register_dataset(
        DatasetDescriptor(id="districts", source_type="postgis"), profile_id="pg2")
    monkeypatch.setattr(tools_mod, "spatial_catalog_service", svc)
    yield svc



def test_plan_data_query_explain(v2_catalog):
    reg = _registered_tools()
    adapter = _fake_adapter()
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.return_value = adapter
        res = _run(reg, "plan_data_query", dataset_id="schools", where="students > 100")
    assert res["status"] == "success"
    assert any("Pushdown" in line for line in res["explain"])
    assert res["plan"]["pushed_filters"], "PostGIS filter 应下推"


def test_aggregate_dataset_statistics_mode(v2_catalog):
    reg = _registered_tools()
    adapter = _fake_adapter()
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.return_value = adapter
        res = _run(reg, "aggregate_dataset", dataset_id="schools",
                   aggregate=[{"func": "count"}], group_by=["district"])
    assert res["status"] == "success"
    assert res["result_mode"] == "statistics"
    assert res["pushdown"] is True
    assert res["rows"][0]["district"] == "金牛"
    # 聚合规格必须传入 adapter
    spec = adapter.query.call_args[0][1]
    assert spec.aggregate == [{"func": "count"}]


def test_query_dataset_v2_extras_passthrough(v2_catalog):
    reg = _registered_tools()
    adapter = _fake_adapter(result=QueryResult(
        dataset_id="schools", features=[], data=[{"count": 5}],
        total_count=1, result_mode="statistics",
        metadata={"query_evidence": {"query_fingerprint": "fp1"}},
    ))
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.return_value = adapter
        res = _run(reg, "query_dataset", dataset_id="schools",
                   aggregate=[{"func": "count"}], group_by=["district"],
                   order_by=["students desc"], result_mode="statistics")
    assert res["dataset_id"] == "schools"
    spec = adapter.query.call_args[0][1]
    # extras 挂在 QuerySpec extras（normalize 读取）
    assert spec.model_extra["aggregate"] == [{"func": "count"}]
    assert spec.model_extra["group_by"] == ["district"]


def test_query_federated_data_tool(v2_catalog):
    reg = _registered_tools()
    left = _fake_adapter(result=QueryResult(dataset_id="schools", features=[
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
         "properties": {"name": "s1", "district": "金牛"}},
    ], total_count=1))
    right = _fake_adapter(result=QueryResult(dataset_id="districts", features=[
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
            [[103.9, 29.9], [104.3, 29.9], [104.3, 30.3], [103.9, 30.3], [103.9, 29.9]]]},
         "properties": {"name": "金牛", "pop": 100}},
    ], total_count=1))
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.side_effect = lambda pid, owner=None: {"pg1": left, "pg2": right}.get(pid)
        res = _run(reg, "query_federated_data",
                   left_dataset_id="schools", right_dataset_id="districts",
                   spatial_op="within", group_by_right=["name"],
                   aggregates=[{"func": "count"}])
    assert res["status"] == "success"
    assert res["rows"][0]["name"] == "金牛"
    assert res["rows"][0]["count"] == 1


def test_query_federated_missing_source_typed_error(v2_catalog):
    reg = _registered_tools()
    with patch("app.tools.data_fabric_tools.connection_manager") as cm:
        cm.get_adapter.return_value = None
        res = _run(reg, "query_federated_data",
                   left_dataset_id="nope", right_dataset_id="districts",
                   join_field_left="a", join_field_right="b")
    assert res["status"] == "error"
    assert res["error_type"] == "UNSUPPORTED_SOURCE"
