"""G20 pytest fixtures（owner root / session / MapSpec store / 工厂）。"""
from __future__ import annotations

import uuid

import pytest

from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif


@pytest.fixture
def owner_root(tmp_path, monkeypatch):
    root = tmp_path / "raster_owner"
    root.mkdir()
    monkeypatch.setattr(
        "app.services.scoped_raster_paths.scoped_raster_roots",
        lambda **kwargs: [root],
    )
    return root


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    """单元级 store 没有 Conversation 行；归属由 HTTP 层守卫单独测试。"""
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


@pytest.fixture
def session_id():
    return "sess-g20-" + uuid.uuid4().hex[:12]


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    return root


@pytest.fixture
def tif_factory():
    return write_tif


@pytest.fixture
def ref_register():
    return register_raster_ref
