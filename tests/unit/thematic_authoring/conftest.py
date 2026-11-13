"""G33 pytest fixtures（MapSpec store 隔离 / 会话 / 统计缓存清理）。"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True)
def _owner_kind(monkeypatch):
    """单元级 store 没有 Conversation 行；归属由 HTTP 层守卫单独测试。"""
    monkeypatch.setattr(
        "app.services.session_data_protocol._conversation_owner_kind",
        lambda sid: "none",
    )


@pytest.fixture
def session_id():
    return "sess-g33-" + uuid.uuid4().hex[:12]


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    import app.services.mapspec.store as store_module

    root = tmp_path / "mapspec_store"
    root.mkdir()
    monkeypatch.setattr(store_module, "BASE_STORAGE_DIR", root, raising=False)
    yield root


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    from app.services.thematic_authoring import stats as stats_mod

    stats_mod._stats_cache.clear()
    yield
    stats_mod._stats_cache.clear()
