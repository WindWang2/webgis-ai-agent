"""DATA-01 regression: federated catalog must not leak fabric datasets.

Deep-review swarm 2026-09-19 (DATA-01): ``DataCatalog`` loaded the in-process
fabric catalog with ``list_datasets()`` (owner=None = every tenant), so a
dataset registered by session A was visible to session B through the
``list_datasets``/``search_datasets`` tools. The fabric source is now loaded
with the caller's session owner and excluded entirely without an owner context.
"""
from __future__ import annotations

import pytest

from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_catalog.catalog import CatalogFilter, get_data_catalog
from app.services.data_fabric.spatial_catalog import spatial_catalog_service
from app.tools import data_discovery as dd
from app.tools.registry import ToolRegistry

_SESSION_A = "data01-session-a"
_SESSION_B = "data01-session-b"
_DATASET_A = "data01-a-only"
_DATASET_GLOBAL = "data01-global"


@pytest.fixture()
def fabric_entries():
    spatial_catalog_service.register_dataset(
        DatasetDescriptor(id=_DATASET_A, source_type="postgis", title="A private"),
        owner=_SESSION_A,
    )
    spatial_catalog_service.register_dataset(
        DatasetDescriptor(id=_DATASET_GLOBAL, source_type="postgis", title="shared"),
        owner=None,
    )
    try:
        yield
    finally:
        spatial_catalog_service.unregister_dataset(_DATASET_A)
        spatial_catalog_service.unregister_dataset(_DATASET_GLOBAL)


async def test_fabric_search_is_owner_scoped(fabric_entries):
    res_b = await get_data_catalog().search(
        filter_=CatalogFilter(scope="fabric"), owner=_SESSION_B
    )
    ids_b = {e.entry_id for e in res_b.entries}
    assert f"fabric:{_DATASET_A}" not in ids_b
    assert f"fabric:{_DATASET_GLOBAL}" in ids_b

    res_a = await get_data_catalog().search(
        filter_=CatalogFilter(scope="fabric"), owner=_SESSION_A
    )
    assert f"fabric:{_DATASET_A}" in {e.entry_id for e in res_a.entries}


async def test_fabric_excluded_without_owner_context(fabric_entries):
    res = await get_data_catalog().search(filter_=CatalogFilter(scope="fabric"))
    assert res.entries == []
    assert all(s.source != "fabric" for s in res.sources_queried)


async def test_tool_path_is_session_scoped(fabric_entries, monkeypatch):
    reg = ToolRegistry()
    dd.register_data_discovery_tools(reg)
    monkeypatch.setattr(dd, "_resolve_session_id", lambda sid: sid)

    tool = reg._tools["list_datasets"]
    from_b = await tool(session_id=_SESSION_B)
    ids_b = {d["id"] for d in from_b["datasets"]}
    assert f"fabric:{_DATASET_A}" not in ids_b
    assert f"fabric:{_DATASET_GLOBAL}" in ids_b

    from_a = await tool(session_id=_SESSION_A)
    ids_a = {d["id"] for d in from_a["datasets"]}
    assert f"fabric:{_DATASET_A}" in ids_a
