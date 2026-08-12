"""Catalog sync efficiency tests (Section 30/31): batch DB lookup (no N+1),
bounded concurrency, and incremental fingerprint skip."""
from unittest.mock import MagicMock

from app.schemas.data_fabric_schema import DatasetDescriptor
from app.services.data_fabric.manager import DataFabricManager


class _FakeAdapter:
    """Minimal adapter: returns a fixed dataset list + descriptors."""

    def __init__(self, datasets):
        self._datasets = datasets

    def list_datasets(self):
        return [{"id": d.id, "title": d.title} for d in self._datasets]

    def describe(self, name):
        for d in self._datasets:
            if d.id == name:
                return d
        return DatasetDescriptor(id=name)


def _mock_db(ds_model, existing_items=None):
    existing_items = existing_items or []
    db = MagicMock()

    ds_q = MagicMock()
    ds_q.filter.return_value.first.return_value = ds_model
    cat_q = MagicMock()
    cat_q.filter.return_value.all.return_value = list(existing_items)
    # DataSourceModel lookup first, then the batch CatalogItemModel lookup.
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


def test_sync_uses_one_batched_catalog_query_not_n_plus_one(monkeypatch):
    """The batch lookup must issue a SINGLE CatalogItemModel query regardless of
    how many datasets the source exposes (no per-item N+1)."""
    datasets = [DatasetDescriptor(id=f"ds_{i}", title=f"D{i}") for i in range(50)]
    db = _mock_db(_ds_model())

    monkeypatch.setattr(DataFabricManager, "get_adapter", staticmethod(lambda profile: _FakeAdapter(datasets)))

    DataFabricManager.sync_catalog(db, "src1")

    # Exactly two db.query calls: DataSourceModel, then the ONE batch
    # CatalogItemModel query. No per-item SELECTs.
    assert db.query.call_count == 2
    db.commit.assert_called_once()


def test_sync_writes_descriptor_fingerprint_on_new_items(monkeypatch):
    datasets = [DatasetDescriptor(id="ds_a", title="A")]
    db = _mock_db(_ds_model())

    monkeypatch.setattr(DataFabricManager, "get_adapter", staticmethod(lambda profile: _FakeAdapter(datasets)))

    DataFabricManager.sync_catalog(db, "src1")

    added = [c for c in db.add.call_args_list][0]
    new_item = added.args[0]
    assert new_item.fingerprint  # non-empty descriptor fingerprint stored


def test_incremental_sync_skips_unchanged_rows(monkeypatch):
    """A second sync whose descriptors produce the same fingerprint must NOT
    rewrite the row (updated_at untouched) — the incremental skip."""
    from app.services.data_fabric.fingerprint import dataset_fingerprint_service

    class _Row:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    desc = DatasetDescriptor(id="ds_a", title="A", geometry_type=None)  # -> "unknown"
    fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(desc)
    existing = _Row(
        id="cat_src1_ds_a",
        source_id="src1",
        name="ds_a",
        fingerprint=fp,
        geometry_type="unknown",
        title="OLD TITLE",
        updated_at="old_ts",
    )

    db = _mock_db(_ds_model(), existing_items=[existing])
    monkeypatch.setattr(DataFabricManager, "get_adapter", staticmethod(lambda profile: _FakeAdapter([desc])))

    DataFabricManager.sync_catalog(db, "src1")

    # Skipped: row untouched (title/updated_at unchanged), nothing added.
    assert existing.title == "OLD TITLE"
    assert existing.updated_at == "old_ts"
    db.add.assert_not_called()
