"""G41 pytest fixtures（对齐 G33 的隔离纪律）。"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


@pytest.fixture
def session_id():
    return "sess-g41-" + uuid.uuid4().hex[:12]


@pytest.fixture(autouse=True)
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    yield root


@pytest.fixture(autouse=True)
def _clear_g41_cache():
    from app.services.bivariate_reliability_authoring import cache
    cache.clear()
    yield
    cache.clear()
