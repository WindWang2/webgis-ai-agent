"""DS1 end-to-end demo (ADR-0171): adding a source = adding one YAML file.

Proves the acceptance "加新源无需改 Python 代码": a brand-new source YAML
dropped into a temp sources dir is loaded by the registry, built into a real
adapter through the offline fixture layer, and synced into the in-process
spatial catalog — zero Python edits.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.data_fabric.source_registry import SourceRegistryService


@pytest.fixture(autouse=True)
def _isolate_spatial_catalog():
    """The demo syncs into the process-wide spatial catalog — clean it around
    the test so later tests (alphabetically: test_catalog) see a pristine
    singleton."""
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    spatial_catalog_service.clear()
    yield
    spatial_catalog_service.clear()


def test_new_source_via_yaml_only(tmp_path: Path, monkeypatch):
    # 1. a brand-new source declaration (the "user" action)
    (tmp_path / "demo_source.yaml").write_text(yaml.safe_dump({
        "source_id": "demo_new_ogc",
        "name": "Demo New OGC",
        "protocol": "ogc_api",
        "endpoint": "https://ads-fixture.invalid/ogc",
        "operations": ["list", "describe", "query"],
        "pushdown": {"bbox": True},
        "quota": {"requests_per_minute": 60},
        "license": "CC-BY-4.0",
        "verified": False,
        "datasets": [],
    }), encoding="utf-8")

    service = SourceRegistryService(tmp_path).load()
    assert service.get("demo_new_ogc").protocol == "ogc_api"

    # 2. no Python change: build the adapter through the fabric registry
    adapter = service.build_adapter("demo_new_ogc")
    assert adapter.profile.source_type == "ogc_api"

    # 3. serve the protocol offline and sync into the catalog
    from tests.data.fabric_fixtures import _feature, ogc_collections_doc  # noqa: PLC0415
    from tests.fixtures.data_fabric.fake_server import make_response, session_with_fake  # noqa: PLC0415

    features = [_feature("1"), _feature("2"), _feature("3")]

    def handler(request):
        if "/items" in request.url:
            return make_response(request.url, json_body={
                "type": "FeatureCollection", "features": features,
                "numberMatched": len(features),
            })
        if "/collections/demo_layer" in request.url:
            return make_response(request.url, json_body=ogc_collections_doc(["demo_layer"])["collections"][0])
        return make_response(request.url, json_body=ogc_collections_doc(["demo_layer"]))

    session = session_with_fake(("/ogc", handler))
    import importlib
    import pkgutil

    import app.services.data_fabric.adapters as adapter_pkg

    for mod_info in pkgutil.iter_modules(adapter_pkg.__path__):
        mod = importlib.import_module(f"app.services.data_fabric.adapters.{mod_info.name}")
        if hasattr(mod, "make_safe_session"):
            monkeypatch.setattr(mod, "make_safe_session", lambda *a, **k: session)

    report = service.sync_source("demo_new_ogc")
    assert report["status"] == "synced"
    assert report["count"] >= 1

    # 4. catalog visibility: the synced dataset is now listed alongside
    #    code-registered sources (unified list_datasets visibility)
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    entries = spatial_catalog_service.list_datasets()
    assert any((e.get("id") if isinstance(e, dict) else getattr(e, "id", "")) == "demo_layer"
               for e in entries), f"demo_layer not visible in catalog: {entries[:5]}"
