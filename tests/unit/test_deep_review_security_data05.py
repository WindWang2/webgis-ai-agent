"""DATA-05 regression: transient describe failure must not mark unavailable.

Deep-review swarm 2026-09-19 (DATA-05): ``sync_catalog`` skipped describe
failures yet also excluded those names from ``seen_ids`` — the removal pass
then marked a live dataset ``unavailable`` as if the source had dropped it.
Now only names absent from the listing are marked unavailable.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_fabric.manager import DataFabricManager
from app.services.data_fabric.metadata_cache import _describe_cache


class _FakeAdapter:
    def __init__(self, datasets, failing: set[str] | None = None):
        self._datasets = datasets
        self._failing = failing or set()

    def list_datasets(self):
        return [{"id": d.id, "title": d.title} for d in self._datasets]

    def describe(self, name):
        if name in self._failing:
            raise TimeoutError(f"describe timed out for {name}")
        for d in self._datasets:
            if d.id == name:
                return d
        return DatasetDescriptor(id=name)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _mock_db(ds_model, existing_items=None):
    existing_items = existing_items or []
    db = MagicMock()
    ds_q = MagicMock()
    ds_q.filter.return_value.first.return_value = ds_model
    cat_q = MagicMock()
    cat_q.filter.return_value.all.return_value = list(existing_items)
    db.query.side_effect = [ds_q, cat_q]
    return db


def _ds_model():
    ds = MagicMock()
    ds.id = "src1"
    ds.name = "src"
    ds.source_type = "ogc_api"
    ds.endpoint_url = "https://example.org"
    ds.connection_profile = {"options": {}, "allow_private": False}
    return ds


@pytest.fixture(autouse=True)
def _clear_describe_cache():
    _describe_cache.invalidate()
    yield
    _describe_cache.invalidate()


def test_transient_describe_failure_keeps_live_dataset_available(monkeypatch):
    live = DatasetDescriptor(id="live", title="live dataset")
    listed = [live]  # "gone" disappears from the listing
    existing_live = _Row(
        id="cat_src1_live", source_id="src1", name="live",
        fingerprint="fp", geometry_type="unknown", availability="available",
        updated_at="old", title="live",
    )
    existing_gone = _Row(
        id="cat_src1_gone", source_id="src1", name="gone",
        fingerprint="fp", geometry_type="unknown", availability="available",
        updated_at="old", title="gone",
    )
    db = _mock_db(_ds_model(), existing_items=[existing_live, existing_gone])
    adapter = _FakeAdapter(listed, failing={"live"})
    monkeypatch.setattr(
        DataFabricManager, "get_adapter", staticmethod(lambda profile: adapter)
    )

    result = DataFabricManager.sync_catalog(db, "src1")

    assert existing_live.availability == "available", (
        "transient describe failure must not mark a listed dataset unavailable"
    )
    assert existing_gone.availability == "unavailable"
    assert result["removed"] == 1
    assert any("live" in w and "describe failed" in w for w in result["warnings"])
    assert result["counts"]["describe_failures"] == 1
