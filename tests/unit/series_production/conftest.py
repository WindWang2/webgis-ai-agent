"""series_production 测试公共 fixtures（store/exports 隔离 + 归属放行）。"""
from __future__ import annotations

import pytest


@pytest.fixture()
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    yield root


@pytest.fixture()
def exports_dir(tmp_path, monkeypatch):
    from app.services import export_paths

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(export_paths, "exports_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    from app.services.thematic_authoring import stats as stats_mod

    stats_mod._stats_cache.clear()
    yield
    stats_mod._stats_cache.clear()
